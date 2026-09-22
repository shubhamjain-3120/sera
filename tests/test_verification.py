from app.verification import deterministic_validate


def test_deterministic_validation_checks_type_choice_and_provenance():
    payload = {
        "targets": [
            {
                "field": {"id": "limit", "field_type": "choice", "options": ["300,000"], "writable": True},
                "selected_candidate_id": "candidate",
                "candidates": [{
                    "id": "candidate", "value": "250,000", "canonical_value": "250,000",
                    "write_value": "250000", "origin": "evidence", "provenance": [],
                }],
            }
        ],
        "repeating_groups": [],
    }
    report = deterministic_validate(payload)
    codes = {finding["code"] for finding in report["findings"]}
    assert report["status"] == "fail"
    assert {"invalid_write_value", "invalid_choice", "missing_provenance"} <= codes


def test_deterministic_validation_flags_required_blank_but_not_optional_blank():
    payload = {
        "targets": [
            {"field": {"id": "required", "field_type": "text", "required": True, "writable": True}, "selected_candidate_id": None, "candidates": []},
            {"field": {"id": "optional", "field_type": "text", "required": False, "writable": True}, "selected_candidate_id": None, "candidates": []},
        ],
        "repeating_groups": [],
    }
    report = deterministic_validate(payload)
    assert [finding["target_id"] for finding in report["findings"]] == ["required"]
    assert report["findings"][0]["code"] == "required_value_missing"
