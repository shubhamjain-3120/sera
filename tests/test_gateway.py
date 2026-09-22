from types import SimpleNamespace

from pydantic import BaseModel

from app.config import Settings
from app.gateway import ModelGateway, Outcome, Stage


class Answer(BaseModel):
    verdict: str


def gateway(**overrides: object) -> ModelGateway:
    settings = Settings(openai_api_key="test-key", model_mapping="test-model", **overrides)
    return ModelGateway(settings)


def respond(instance: ModelGateway) -> object:
    return instance.respond(
        Stage.MAPPING,
        Answer,
        "Decide.",
        {"fact": "value"},
        prompt_version="test-v1",
        input_reference="sha256:abc",
    )


class FakeResponses:
    def __init__(self, response: object | Exception):
        self.response = response
        self.calls: list[dict] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def client_returning(response: object | Exception) -> SimpleNamespace:
    return SimpleNamespace(responses=FakeResponses(response))


def completed(**overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "id": "resp_1",
        "status": "completed",
        "output": [],
        "output_parsed": Answer(verdict="supported"),
        "usage": SimpleNamespace(input_tokens=11, output_tokens=3, total_tokens=14),
    }
    return SimpleNamespace(**{**fields, **overrides})


def test_the_suite_never_runs_against_a_real_provider():
    """A developer's .env holds a live key; the suite must not pick it up."""
    assert ModelGateway().enabled is False


def test_missing_credentials_disable_the_gateway_without_calling_a_provider():
    instance = ModelGateway(Settings(openai_api_key=None))
    result = respond(instance)
    assert not instance.enabled
    assert result.outcome is Outcome.DISABLED
    assert result.parsed is None
    assert result.ok is False


def test_successful_call_returns_parsed_output_and_a_full_trace():
    instance = gateway()
    instance._client = client_returning(completed())

    result = respond(instance)

    assert result.ok
    assert result.parsed.verdict == "supported"
    assert result.trace["model"] == "test-model"
    assert result.trace["provider"] == "openai"
    assert result.trace["stage"] == "mapping"
    assert result.trace["prompt_version"] == "test-v1"
    assert result.trace["input_reference"] == "sha256:abc"
    assert result.trace["usage"]["total_tokens"] == 14
    assert result.trace["duration_ms"] >= 0


def test_each_stage_reads_its_own_configured_model():
    instance = gateway(model_verification="verifier-model")
    instance._client = client_returning(completed())
    instance.respond(
        Stage.VERIFICATION, Answer, "Check.", {}, prompt_version="v", input_reference="ref"
    )
    assert instance._client.responses.calls[0]["model"] == "verifier-model"


def test_reasoning_effort_is_sent_and_recorded():
    instance = gateway(model_reasoning_effort="max")
    instance._client = client_returning(completed())

    result = respond(instance)

    assert instance._client.responses.calls[0]["reasoning"] == {"effort": "max"}
    assert result.trace["reasoning_effort"] == "max"


def test_blank_reasoning_effort_omits_the_parameter():
    instance = gateway(model_reasoning_effort="")
    instance._client = client_returning(completed())

    result = respond(instance)

    assert "reasoning" not in instance._client.responses.calls[0]
    assert "reasoning_effort" not in result.trace


def test_refusal_is_reported_rather_than_treated_as_an_answer():
    refused = completed(
        output=[SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="Cannot comply")])]
    )
    instance = gateway()
    instance._client = client_returning(refused)

    result = respond(instance)

    assert result.outcome is Outcome.REFUSED
    assert result.parsed is None
    assert result.trace["refusal"] == "Cannot comply"


def test_incomplete_response_is_not_used():
    truncated = completed(
        status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens")
    )
    instance = gateway()
    instance._client = client_returning(truncated)

    result = respond(instance)

    assert result.outcome is Outcome.INCOMPLETE
    assert result.trace["incomplete_reason"] == "max_output_tokens"


def test_missing_parsed_output_is_invalid():
    instance = gateway()
    instance._client = client_returning(completed(output_parsed=None))
    assert respond(instance).outcome is Outcome.INVALID


def test_transport_failure_is_captured_as_a_trace_not_an_exception():
    instance = gateway()
    instance._client = client_returning(TimeoutError("upstream timed out"))

    result = respond(instance)

    assert result.outcome is Outcome.ERROR
    assert "TimeoutError" in result.trace["error"]
    assert result.trace["duration_ms"] >= 0
