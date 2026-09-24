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
            output=MappingOutput(answers=[MappingAnswer(field_id="name", value="Alice")]),
            model="gpt-6-sol", prompt_version="prompt", schema_version="schema",
            execution_id="execution-1",
        )


def test_default_model_profile_uses_sol_high_standard():
    settings = Settings(_env_file=None)
    assert settings.openai_evidence_model == settings.openai_mapping_model == "gpt-6-sol"
    assert settings.openai_reasoning_effort == "high"
    assert settings.openai_service_tier == "default"
    assert _mapping_settings().openai_service_tier == "default"


def test_mapping_sends_compact_fields_and_facts_and_returns_only_field_values():
    gateway = Gateway()
    schema = {"fields": [
        {"id": "name", "label": "Applicant name", "native_name": "Text 1",
         "field_type": "text", "location": {"page": 1}, "label_evidence": [{"text": "Name"}]},
        {"id": "unsupported", "label": "Existing value", "current_value": "already filled"},
    ]}
    snapshots = [("snap-1", {"facts": [{"id": "fact-1", "value": "Alice"}],
                             "parse_blocks": [{"id": "block-1", "text": "Alice"}]})]

    result = map_form(schema, snapshots, session=None, run_id="run-1", gateway=gateway)

    assert len(gateway.calls) == 1
    stage, payload, output_type, kwargs = gateway.calls[0]
    assert stage == "mapping"
    assert payload["fields"] == [{"id": "name", "description": "Applicant name (Name)"}]
    assert payload["evidence"] == [{"id": "fact-1", "value": "Alice"}]
    assert "evidence_parse_blocks" not in payload
    assert output_type is MappingOutput
    assert output_type.model_json_schema()["properties"].keys() == {"answers"}
    assert output_type.model_json_schema()["$defs"]["MappingAnswer"]["properties"].keys() == {"field_id", "value"}
    assert set(output_type.model_json_schema()["$defs"]["MappingAnswer"]["required"]) == {"field_id", "value"}
    assert kwargs["idempotency_key"] == "mapping:v3:run-1"
    assert result["answers"] == [{"field_id": "name", "write_value": "Alice"}]
    assert result["model_profile"]["reasoning_effort"] == "high"
    assert result["model_profile"]["service_tier"] == "fast"
