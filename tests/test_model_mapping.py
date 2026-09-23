from types import SimpleNamespace

from app.mapping import MappingAnswer, MappingOutput, _mapping_settings, map_form
from app.config import Settings


class Gateway:
    settings = SimpleNamespace(openai_reasoning_effort="high", openai_service_tier="fast")

    def __init__(self):
        self.calls = []

    def run(self, stage, payload, output_type, **kwargs):
        self.calls.append((stage, payload, output_type, kwargs))
        return SimpleNamespace(
            output=MappingOutput(answers=[
                MappingAnswer(field_id="name", write_value="Alice", evidence_fact_ids=["fact-1"])
            ]),
            model="gpt-6-sol", prompt_version="prompt", schema_version="schema",
            execution_id="execution-1",
        )


def test_default_model_profile_uses_sol_high_standard():
    settings = Settings(_env_file=None)
    assert settings.openai_evidence_model == settings.openai_mapping_model == "gpt-6-sol"
    assert settings.openai_reasoning_effort == "high"
    assert settings.openai_service_tier == "default"
    assert _mapping_settings().openai_service_tier == "default"


def test_mapping_sends_all_fields_and_evidence_in_one_call():
    gateway = Gateway()
    schema = {"fields": [{"id": "name"}, {"id": "unsupported"}]}
    snapshots = [("snap-1", {"facts": [{"id": "fact-1", "value": "Alice"}],
                             "parse_blocks": [{"id": "block-1", "text": "Alice"}]})]

    result = map_form(schema, snapshots, session=None, run_id="run-1", gateway=gateway)

    assert len(gateway.calls) == 1
    stage, payload, output_type, kwargs = gateway.calls[0]
    assert stage == "mapping"
    assert [field["id"] for field in payload["template_fields"]] == ["name", "unsupported"]
    assert payload["evidence_facts"][0]["id"] == "fact-1"
    assert payload["evidence_parse_blocks"][0]["text"] == "Alice"
    assert output_type is MappingOutput
    assert kwargs["idempotency_key"] == "mapping:run-1"
    assert result["answers"] == [{"field_id": "name", "write_value": "Alice",
                                  "evidence_fact_ids": ["fact-1"], "source_block_ids": [],
                                  "assumption": None}]
    assert result["model_profile"]["reasoning_effort"] == "high"
    assert result["model_profile"]["service_tier"] == "fast"
