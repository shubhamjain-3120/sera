"""Deterministic Phase 5 review acceptance gate."""

import json

from app.review import apply_review_decision
from app.verification import EvidenceAwareVerifier, deterministic_validate


def main() -> None:
    candidate = {
        "id": "candidate-name",
        "target_field_id": "name",
        "value": "Amina Doe",
        "origin": "evidence",
        "resolution": "direct",
        "selectable": True,
        "provenance": [{"kind": "text_span", "artifact_id": "source"}],
        "review_required": True,
    }
    payload = {
        "targets": [{"field": {"id": "name", "label": "Name", "field_type": "text", "required": True, "writable": True}, "selected_candidate_id": "candidate-name", "candidates": [candidate], "issues": [], "state": "proposed"}],
        "issues": [],
        "repeating_groups": [],
        "template_schema": {"inspection": {"format": "pdf"}},
    }
    reviewed, audit = apply_review_decision(payload, decision_id="evaluation", target_field_id="name", action="approve", actor="evaluator", reason=None, candidate_id=None, value=None)
    result = {
        "terminal_human_origin": reviewed["targets"][0]["review"]["origin"] == "human",
        "prior_candidate_preserved": len(reviewed["targets"][0]["candidates"]) == 2,
        "deterministic_status": deterministic_validate(reviewed)["status"],
        "semantic_findings_after_human_decision": len(EvidenceAwareVerifier().verify(reviewed, [{"facts": []}])["findings"]),
        "audit_value_preserved": audit["new_value"] == "Amina Doe",
    }
    print(json.dumps(result, indent=2))
    if not all(value in {True, "pass", 0} for value in result.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
