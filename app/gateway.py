"""The Model Gateway: the only place the application talks to a model provider.

Every stage goes through `ModelGateway.respond`, which returns a structured
result alongside an execution trace. Refusals, incomplete responses, and
transport errors are all reported as an outcome rather than raised, so a caller
can always fall back to its deterministic path instead of failing a run.
"""

import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

GATEWAY_VERSION = "model-gateway-v1"
PROVIDER = "openai"

Structured = TypeVar("Structured", bound=BaseModel)


class Stage(StrEnum):
    TEMPLATE_ANALYSIS = "template_analysis"
    EVIDENCE_EXTRACTION = "evidence_extraction"
    MAPPING = "mapping"
    VERIFICATION = "verification"


class Outcome(StrEnum):
    OK = "ok"
    DISABLED = "disabled"
    REFUSED = "refused"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"
    ERROR = "error"


@dataclass(frozen=True)
class ModelResult:
    """A structured model result and everything needed to audit how it arose."""

    outcome: Outcome
    parsed: BaseModel | None
    trace: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK and self.parsed is not None


def _model_for(settings: Settings, stage: Stage) -> str:
    return {
        Stage.TEMPLATE_ANALYSIS: settings.model_template_analysis,
        Stage.EVIDENCE_EXTRACTION: settings.model_evidence_extraction,
        Stage.MAPPING: settings.model_mapping,
        Stage.VERIFICATION: settings.model_verification,
    }[stage]


class ModelGateway:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key)

    def _openai(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_base_url,
                timeout=self.settings.model_timeout_seconds,
                max_retries=self.settings.model_max_retries,
            )
        return self._client

    def warm(self) -> None:
        """Build the client up front so concurrent batches do not race on it."""
        if self.enabled:
            self._openai()

    def batches(self, items: list[Any]) -> list[list[Any]]:
        size = max(1, self.settings.model_batch_size)
        return [items[index : index + size] for index in range(0, len(items), size)]

    def respond(
        self,
        stage: Stage,
        schema: type[Structured],
        instructions: str,
        payload: dict[str, Any],
        *,
        prompt_version: str,
        input_reference: str,
    ) -> ModelResult:
        model = _model_for(self.settings, stage)
        trace: dict[str, Any] = {
            "gateway_version": GATEWAY_VERSION,
            "provider": PROVIDER,
            "model": model,
            "stage": stage.value,
            "prompt_version": prompt_version,
            "schema_version": f"{schema.__name__}-v1",
            "input_reference": input_reference,
        }
        if not self.enabled:
            return ModelResult(Outcome.DISABLED, None, {**trace, "reason": "No model credentials are configured"})

        effort = self.settings.model_reasoning_effort
        if effort:
            trace["reasoning_effort"] = effort
        started = time.perf_counter()
        try:
            response = self._openai().responses.parse(
                model=model,
                instructions=instructions,
                # Document-derived content is data, never instruction. It is
                # fenced as JSON in the user turn and the schema constrains what
                # the model is allowed to return.
                input=[{"role": "user", "content": json.dumps(payload, default=str, sort_keys=True)}],
                text_format=schema,
                **({"reasoning": {"effort": effort}} if effort else {}),
            )
        except Exception as exc:
            return ModelResult(
                Outcome.ERROR,
                None,
                {**trace, "duration_ms": _elapsed(started), "error": f"{type(exc).__name__}: {exc}"},
            )

        trace["duration_ms"] = _elapsed(started)
        trace["usage"] = _usage(response)
        trace["response_id"] = getattr(response, "id", None)

        refusal = _refusal(response)
        if refusal:
            return ModelResult(Outcome.REFUSED, None, {**trace, "refusal": refusal})
        if getattr(response, "status", "completed") == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
            return ModelResult(Outcome.INCOMPLETE, None, {**trace, "incomplete_reason": reason})

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            return ModelResult(Outcome.INVALID, None, {**trace, "error": "Response carried no parsed output"})
        try:
            validated = schema.model_validate(parsed)
        except ValidationError as exc:
            return ModelResult(Outcome.INVALID, None, {**trace, "error": str(exc)})
        return ModelResult(Outcome.OK, validated, trace)


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _refusal(response: Any) -> str | None:
    for item in getattr(response, "output", None) or []:
        for piece in getattr(item, "content", None) or []:
            if getattr(piece, "type", None) == "refusal":
                return getattr(piece, "refusal", "Model refused the request")
    return None
