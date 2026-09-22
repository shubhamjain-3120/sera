"""Legacy command name retained for deterministic Fill Plan validation checks."""

import json

from app.verification import deterministic_validate


def main() -> None:
    payload = {
        "targets": [
            {"field": {"id": "required", "field_type": "text", "required": True, "writable": True}, "selected_candidate_id": None, "candidates": []},
            {"field": {"id": "optional", "field_type": "text", "required": False, "writable": True}, "selected_candidate_id": None, "candidates": []},
        ],
        "repeating_groups": [],
    }
    result = deterministic_validate(payload)
    output = {"validation": result, "optional_blank_is_quiet": all(item["target_id"] != "optional" for item in result["findings"])}
    print(json.dumps(output, indent=2))
    if not output["optional_blank_is_quiet"] or not any(item["target_id"] == "required" for item in result["findings"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
