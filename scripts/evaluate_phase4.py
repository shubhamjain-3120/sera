"""Measure Phase 4 detection and false rejection on controlled acceptance cases."""

import json

from app.verification import build_verification_report
from tests.test_verification import candidate, evidence_fact, payload, target


def main() -> None:
    cases: list[tuple[str, dict, list[dict], str, str | None]] = []
    valid = evidence_fact("valid", "applicant.legal_name", "Amina Doe")
    cases.append(("valid proposal", payload(target("name", "applicant.legal_name", candidate(valid, "name"))), [{"facts": [valid]}], "pass", None))
    wrong_entity = evidence_fact("wrong-entity", "applicant.legal_name", "Driver Doe", role="driver")
    cases.append(("incorrect entity", payload(target("name", "applicant.legal_name", candidate(wrong_entity, "name"))), [{"facts": [wrong_entity]}], "fail", "incorrect_entity"))
    old = evidence_fact("old", "business.revenue", "100", role="business", period="2025")
    cases.append(("reporting period", payload(target("revenue", "business.revenue", candidate(old, "revenue"), constraints={"reporting_period": "2026"})), [{"facts": [old]}], "fail", "reporting_period_mismatch"))
    missed = evidence_fact("missed", "applicant.phone", "555-0100")
    cases.append(("missed fact", payload(target("phone", "applicant.phone", None, required=False)), [{"facts": [missed]}], "needs_review", "missed_evidence"))
    derived = {
        "id": "depth-three",
        "target_field_id": "total",
        "fact_id": None,
        "value": 42,
        "origin": "derivation",
        "resolution": "derived",
        "provenance": [],
        "review_required": True,
        "derivation": {"depth": 3, "input_ids": ["depth-two"], "operation": "sum"},
    }
    derived_plan = payload(target("total", "business.total", derived))
    derived_plan["targets"][0]["field"]["field_type"] = "number"
    cases.append(("unsupported derivation", derived_plan, [{"facts": []}], "fail", "unsupported_derivation"))
    results = []
    detected = 0
    expected_errors = 0
    false_rejections = 0
    for name, plan, snapshots, expected_status, expected_code in cases:
        report = build_verification_report(plan, snapshots)
        findings = report["deterministic"]["findings"] + report["independent"]["findings"]
        codes = [item["code"] for item in findings]
        if expected_code:
            expected_errors += 1
            detected += expected_code in codes
        elif report["status"] != "pass":
            false_rejections += 1
        results.append({"case": name, "expected": expected_status, "actual": report["status"], "finding_codes": codes, "selection_unchanged": report["selection_unchanged"]})
    output = {"cases": results, "error_detection_rate": detected / expected_errors, "false_rejection_rate": false_rejections / max(1, len(cases) - expected_errors)}
    print(json.dumps(output, indent=2))
    if any(item["expected"] != item["actual"] or not item["selection_unchanged"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
