"""Central, traced OpenAI Responses API boundary for structured model work."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.models import ModelExecution, new_id

OutputT = TypeVar("OutputT", bound=BaseModel)
PROMPT_VERSIONS = {"evidence": "evidence-extraction-v1", "mapping": "mapping-v1"}
SCHEMA_VERSIONS = {"evidence": "evidence-facts-v1", "mapping": "mapping-v1"}
RETRYABLE = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
FAST_SERVICE_TIERS = frozenset({"fast", "priority"})


@dataclass(frozen=True)
class ModelResult:
    output: BaseModel
    execution_id: str
    model: str
    prompt_version: str
    schema_version: str


class GatewayError(RuntimeError):
    def __init__(self, message: str, execution_id: str | None = None):
        super().__init__(message)
        self.execution_id = execution_id


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _sha256(value: Any) -> str:
    encoded = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _response_details(response: Any) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    refusal = None
    incomplete = None
    usage = getattr(response, "usage", None)
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            if getattr(content, "type", None) == "refusal":
                refusal = getattr(content, "refusal", None) or "Model refused the request"
    if getattr(response, "status", None) == "incomplete":
        incomplete = _jsonable(getattr(response, "incomplete_details", None)) or {"reason": "incomplete"}
    return (
        refusal,
        incomplete,
        _jsonable(usage) if usage is not None else None,
    )


class ModelGateway:
    """Model gateway. Trace writes commit independently so callers can roll back safely."""

    def __init__(self, settings: Settings | None = None, client: Any | None = None):
        self.settings = settings or get_settings()
        if client is not None:
            self.client = client
        else:
            if not self.settings.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY must be configured for model-backed processing")
            options: dict[str, Any] = {
                "api_key": self.settings.openai_api_key,
                "timeout": self.settings.model_gateway_timeout_seconds,
                "max_retries": 0,
            }
            if self.settings.openai_base_url:
                options["base_url"] = self.settings.openai_base_url
            self.client = OpenAI(**options)

    def model_for(self, stage: str) -> str:
        mapping = {
            "evidence": self.settings.openai_evidence_model,
            "mapping": self.settings.openai_mapping_model,
        }
        try:
            return mapping[stage]
        except KeyError as exc:
            raise ValueError(f"Unknown model stage: {stage}") from exc

    def run(
        self,
        stage: str,
        input_data: Any,
        output_model: type[OutputT],
        *,
        session: Session,
        run_id: str | None = None,
        idempotency_key: str,
        instructions: str,
        prompt_version: str | None = None,
        schema_version: str | None = None,
        validate_output: Callable[[OutputT], None] | None = None,
    ) -> ModelResult:
        model = self.model_for(stage)
        prompt_version = prompt_version or PROMPT_VERSIONS[stage]
        schema_version = schema_version or SCHEMA_VERSIONS[stage]
        input_hash = _sha256(
            {
                "instructions": instructions,
                "input": input_data,
                "provider": "openai",
                "stage": stage,
                "model": model,
                "reasoning_effort": self.settings.openai_reasoning_effort,
                "service_tier": self.settings.openai_service_tier,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "output_schema": output_model.model_json_schema(),
            }
        )
        trace_id = new_id()
        started = time.monotonic()
        retries = 0
        response_id = None
        usage = None
        refusal = None
        incomplete = None
        error = None
        output: OutputT | None = None

        prior = list(
            session.scalars(
                select(ModelExecution).where(
                    (ModelExecution.idempotency_key == idempotency_key)
                    | (ModelExecution.idempotency_key.like(f"{idempotency_key}:attempt:%"))
                )
            )
        )
        if prior:
            if any(item.input_sha256 != input_hash for item in prior):
                raise GatewayError("Idempotency key was reused with different model input", prior[-1].id)
            completed = next((item for item in reversed(prior) if item.error is None), None)
            if completed is not None:
                if completed.output_payload is None:
                    raise GatewayError("Completed model trace has no replayable structured output", completed.id)
                replayed = output_model.model_validate(completed.output_payload)
                if validate_output is not None:
                    validate_output(replayed)
                return ModelResult(
                    replayed,
                    completed.id,
                    completed.model,
                    completed.prompt_version,
                    completed.schema_version,
                )
            idempotency_key = f"{idempotency_key}:attempt:{len(prior) + 1}"

        request_input = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(_jsonable(input_data), ensure_ascii=False)},
        ]
        max_retries = max(0, self.settings.model_gateway_max_retries)
        for attempt in range(max_retries + 1):
            error = None
            refusal = None
            incomplete = None
            usage = None
            response_id = None
            output = None
            try:
                response = self.client.responses.parse(
                    model=model,
                    input=request_input,
                    text_format=output_model,
                    reasoning={"effort": self.settings.openai_reasoning_effort},
                    service_tier=self.settings.openai_service_tier,
                    store=False,
                )
                response_id = getattr(response, "id", None)
                refusal, incomplete, usage = _response_details(response)
                actual_service_tier = getattr(response, "service_tier", None)
                requested_service_tier = self.settings.openai_service_tier
                fast_tier_match = (
                    requested_service_tier in FAST_SERVICE_TIERS
                    and actual_service_tier in FAST_SERVICE_TIERS
                )
                if actual_service_tier != requested_service_tier and not fast_tier_match:
                    error = (
                        "OpenAI served the request with service tier "
                        f"{actual_service_tier!r}; requested {requested_service_tier!r}"
                    )
                elif refusal:
                    error = "Model refused the request"
                elif incomplete:
                    error = "Model response was incomplete"
                elif getattr(response, "status", None) != "completed":
                    error = f"Model response status was {getattr(response, 'status', 'unknown')}"
                else:
                    output = getattr(response, "output_parsed", None)
                    if output is None:
                        error = "Model returned no output matching the required structured schema"
                    elif not isinstance(output, output_model):
                        output = output_model.model_validate(output)
                    if output is not None and validate_output is not None:
                        validate_output(output)
                if error and incomplete and attempt < max_retries:
                    retries += 1
                    continue
                break
            except RETRYABLE as exc:
                error = f"{type(exc).__name__}: {exc}"
                if attempt < max_retries:
                    retries += 1
                    continue
                break
            except Exception as exc:
                # Schema validation and SDK parsing errors are explicit failures;
                # retry them once under the same policy, then persist the trace.
                error = f"{type(exc).__name__}: {exc}"
                output = None
                if attempt < max_retries:
                    retries += 1
                    continue
                break

        duration_ms = round((time.monotonic() - started) * 1000)
        record = ModelExecution(
            id=trace_id,
            idempotency_key=idempotency_key,
            run_id=run_id,
            stage=stage,
            input_sha256=input_hash,
            output_sha256=_sha256(output) if output is not None else None,
            output_payload=output.model_dump(mode="json") if output is not None else None,
            provider="openai",
            model=model,
            reasoning_effort=self.settings.openai_reasoning_effort,
            prompt_version=prompt_version,
            schema_version=schema_version,
            response_id=response_id,
            duration_ms=duration_ms,
            usage=usage,
            retry_count=retries,
            refusal=refusal,
            incomplete_details=incomplete,
            error=error,
        )
        self._persist_independently(session, record)
        if output is None or error is not None:
            details = f"refusal={refusal}; incomplete={incomplete}; error={error}"
            raise GatewayError(f"Model gateway failed ({details})", trace_id)
        return ModelResult(output, trace_id, model, prompt_version, schema_version)

    @staticmethod
    def _persist_independently(caller_session: Session, record: ModelExecution) -> None:
        """Commit trace outside the caller's transaction, preserving failure evidence."""
        independent = sessionmaker(bind=caller_session.get_bind(), expire_on_commit=False)()
        try:
            independent.add(record)
            independent.commit()
        except Exception:
            independent.rollback()
            raise
        finally:
            independent.close()
