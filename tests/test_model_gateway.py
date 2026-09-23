from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.model_gateway import GatewayError, ModelGateway
from app.models import ModelExecution


class Answer(BaseModel):
    value: str


class FakeResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session
    engine.dispose()


def response(*, parsed=None, status="completed", output=None, incomplete=None):
    return SimpleNamespace(
        id="resp_123",
        service_tier="default",
        status=status,
        output_parsed=parsed,
        output=output or [],
        incomplete_details=incomplete,
        usage=SimpleNamespace(model_dump=lambda mode=None: {"input_tokens": 9, "output_tokens": 3}),
    )


def gateway(responses, retries=2):
    client = SimpleNamespace(responses=FakeResponses(responses))
    settings = Settings(
        openai_api_key="test", openai_reasoning_effort="high",
        model_gateway_max_retries=retries,
    )
    return ModelGateway(settings, client), client.responses


def test_gateway_uses_responses_structured_output_and_persists_trace(session):
    instance, client = gateway([response(parsed=Answer(value="ok"))])

    result = instance.run(
        "evidence",
        {"input": "document"},
        Answer,
        session=session,
        idempotency_key="test-evidence-1",
        instructions="Extract facts",
    )

    assert result.output.value == "ok"
    assert client.calls[0]["store"] is False
    assert client.calls[0]["reasoning"] == {"effort": "high"}
    assert client.calls[0]["text_format"] is Answer
    trace = session.scalar(select(ModelExecution).where(ModelExecution.id == result.execution_id))
    assert trace is not None
    assert trace.stage == "evidence"
    assert trace.output_sha256
    assert trace.response_id == "resp_123"
    assert trace.usage == {"input_tokens": 9, "output_tokens": 3}

    with pytest.raises(ValueError, match="immutable"):
        trace.error = "changed"
        session.flush()


def test_gateway_retries_incomplete_then_succeeds_and_is_idempotent(session):
    incomplete = response(status="incomplete", incomplete={"reason": "max_output_tokens"})
    instance, client = gateway([incomplete, response(parsed=Answer(value="ok"))])
    result = instance.run(
        "evidence",
        {"input": "document"},
        Answer,
        session=session,
        idempotency_key="test-evidence-2",
        instructions="Extract facts",
    )
    trace = session.get(ModelExecution, result.execution_id)
    assert trace.retry_count == 1
    assert len(client.calls) == 2

    session.rollback()
    replay = instance.run(
        "evidence",
        {"input": "document"},
        Answer,
        session=session,
        idempotency_key="test-evidence-2",
        instructions="Extract facts",
    )
    assert replay.output.value == "ok"
    assert replay.execution_id == result.execution_id
    assert len(client.calls) == 2


def test_gateway_persists_refusal_before_raising(session):
    refusal = SimpleNamespace(type="refusal", refusal="Cannot process this request")
    instance, _client = gateway([response(output=[SimpleNamespace(content=[refusal])])], retries=0)

    with pytest.raises(GatewayError, match="refusal") as failure:
        instance.run(
            "evidence",
            {"input": "document"},
            Answer,
            session=session,
            idempotency_key="test-evidence-3",
            instructions="Extract facts",
        )

    trace = session.get(ModelExecution, failure.value.execution_id)
    assert trace.refusal == "Cannot process this request"
    assert trace.error == "Model refused the request"


def test_failed_gateway_operation_creates_new_immutable_attempt_on_retry(session):
    first, _first_client = gateway(
        [response(status="incomplete", incomplete={"reason": "max_output_tokens"})], retries=0
    )
    with pytest.raises(GatewayError) as failure:
        first.run(
            "evidence",
            {"input": "document"},
            Answer,
            session=session,
            idempotency_key="test-evidence-retry",
            instructions="Extract facts",
        )
    failed_trace = session.get(ModelExecution, failure.value.execution_id)
    assert failed_trace.incomplete_details == {"reason": "max_output_tokens"}

    second, _second_client = gateway([response(parsed=Answer(value="ok"))], retries=0)
    result = second.run(
        "evidence",
        {"input": "document"},
        Answer,
        session=session,
        idempotency_key="test-evidence-retry",
        instructions="Extract facts",
    )
    retried_trace = session.get(ModelExecution, result.execution_id)
    assert retried_trace.idempotency_key == "test-evidence-retry:attempt:2"
    assert retried_trace.error is None


def test_completed_idempotency_key_rejects_changed_input(session):
    instance, client = gateway([response(parsed=Answer(value="ok"))])
    instance.run(
        "evidence",
        {"input": "first"},
        Answer,
        session=session,
        idempotency_key="changed-input-key",
        instructions="Extract facts",
    )

    with pytest.raises(GatewayError, match="different model input"):
        instance.run(
            "evidence",
            {"input": "second"},
            Answer,
            session=session,
            idempotency_key="changed-input-key",
            instructions="Extract facts",
        )
    assert len(client.calls) == 1


def test_output_reference_validation_failure_is_traced_and_raises(session):
    instance, _client = gateway([response(parsed=Answer(value="bad-reference"))], retries=0)

    def validate(_output):
        raise ValueError("unknown semantic reference")

    with pytest.raises(GatewayError, match="unknown semantic reference") as failure:
        instance.run(
            "mapping",
            {"input": "template and facts"},
            Answer,
            session=session,
            idempotency_key="invalid-reference-output",
            instructions="Map facts",
            validate_output=validate,
        )

    trace = session.get(ModelExecution, failure.value.execution_id)
    assert trace.output_payload is None
    assert "unknown semantic reference" in trace.error


def test_independent_trace_write_succeeds_with_file_backed_read_transaction(tmp_path):
    database = tmp_path / "gateway-trace.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as caller_session:
        # Keep a read transaction open on the caller connection while the gateway
        # persists its trace on a separate connection/transaction.
        caller_session.execute(select(ModelExecution)).all()
        instance, _client = gateway([response(parsed=Answer(value="ok"))], retries=0)
        result = instance.run(
            "evidence",
            {"input": "document"},
            Answer,
            session=caller_session,
            idempotency_key="read-transaction-trace",
            instructions="Extract facts",
        )
        caller_session.rollback()

    with Session(engine) as verifier_session:
        trace = verifier_session.get(ModelExecution, result.execution_id)
        assert trace is not None
        assert trace.output_payload == {"value": "ok"}
    engine.dispose()
