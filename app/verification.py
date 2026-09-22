"""Deterministic Fill Plan validation. Historical verifier reports remain stored in the database."""

import hashlib
import re
from typing import Any

from app.mapping import validate_write_value

DETERMINISTIC_VERSION = "deterministic-validation-v2"

def _finding(
    code: str,
    severity: str,
    target_id: str | None,
    message: str,
    *,
    source: str,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    material = f"{source}:{code}:{target_id}:{message}"
    return {
        "id": hashlib.sha1(material.encode()).hexdigest()[:16],
        "source": source,
        "code": code,
        "severity": severity,
        "target_id": target_id,
        "message": message,
        "evidence": evidence or [],
    }


def _tokens(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _selected(target: dict[str, Any]) -> dict[str, Any] | None:
    selected_id = target.get("selected_candidate_id")
    return next(
        (candidate for candidate in target.get("candidates", []) if candidate.get("id") == selected_id),
        None,
    )


def deterministic_validate(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate structural and typed invariants without semantic model judgment."""
    findings: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for target in payload.get("targets", []):
        field = target.get("field", {})
        target_id = field.get("id")
        if not target_id or target_id in seen_targets:
            findings.append(
                _finding(
                    "duplicate_or_missing_target",
                    "blocker",
                    target_id,
                    "Every target must have one unique identifier.",
                    source="deterministic",
                )
            )
            continue
        seen_targets.add(target_id)
        candidate = _selected(target)
        review = target.get("review") or {}
        dependency_notice = review.get("dependency_notice") or {}
        if dependency_notice and not dependency_notice.get("acknowledged"):
            findings.append(
                _finding(
                    "dependency_acknowledgement_required",
                    "blocker",
                    target_id,
                    "A human-approved dependent value must acknowledge its changed input.",
                    source="deterministic",
                )
            )
        if target.get("selected_candidate_id") and candidate is None:
            findings.append(
                _finding(
                    "selected_candidate_missing",
                    "blocker",
                    target_id,
                    "The selected candidate is not present in this immutable revision.",
                    source="deterministic",
                )
            )
            continue
        if candidate is None:
            if field.get("required") and field.get("writable", True):
                findings.append(
                    _finding(
                        "required_value_missing",
                        "blocker",
                        target_id,
                        "A required writable target has no selected value.",
                        source="deterministic",
                    )
                )
            continue
        if not field.get("writable", True) or field.get("field_type") in {"signature", "action"}:
            findings.append(
                _finding(
                    "forbidden_write",
                    "blocker",
                    target_id,
                    "A value is selected for a forbidden or non-writable target.",
                    source="deterministic",
                )
            )
        value = candidate.get("value")
        canonical_value = candidate.get("canonical_value", value)
        write_value = candidate.get("write_value", canonical_value)
        if canonical_value != value:
            findings.append(_finding("canonical_value_mismatch", "blocker", target_id, "Compatibility value alias differs from canonical_value.", source="deterministic"))
        review_action = candidate.get("review_action")
        human_exception = candidate.get("origin") == "human" and review.get("origin") == "human"
        write_issues = validate_write_value(
            field,
            canonical_value,
            write_value,
            allow_blank=human_exception and review_action in {"clear", "intentional_blank"},
            allow_exception=human_exception and review_action == "not_applicable",
        )
        if write_issues:
            findings.append(_finding("invalid_write_value", "blocker", target_id, "; ".join(write_issues), source="deterministic"))
        field_type = field.get("field_type", "unknown")
        required_exception = bool(review.get("required_exception"))
        if field_type == "number" and not required_exception and not isinstance(value, (int, float)):
            findings.append(_finding("type_mismatch", "blocker", target_id, "Selected value is not numeric.", source="deterministic"))
        if field_type == "boolean" and not required_exception and not isinstance(value, bool):
            findings.append(_finding("type_mismatch", "blocker", target_id, "Selected value is not boolean.", source="deterministic"))
        if field_type == "choice" and not required_exception and field.get("options") and value not in field["options"]:
            findings.append(_finding("invalid_choice", "blocker", target_id, "Selected value is not an approved choice.", source="deterministic"))
        if candidate.get("origin") in {"evidence", "agency"} and not candidate.get("provenance"):
            findings.append(_finding("missing_provenance", "blocker", target_id, "Evidence-backed value has no exact source location.", source="deterministic"))
        if candidate.get("origin") == "derivation":
            derivation = candidate.get("derivation") or {}
            if derivation.get("depth", 99) > 2:
                findings.append(_finding("unsupported_derivation", "blocker", target_id, "Derivation exceeds maximum depth two.", source="deterministic"))
            system_approved = candidate.get("approval_state") == "system_approved"
            if not derivation.get("input_ids") or (not candidate.get("review_required") and not system_approved):
                findings.append(_finding("unsupported_derivation", "blocker", target_id, "Derivation lacks grounded inputs or mandatory review.", source="deterministic"))
            if system_approved and (
                candidate.get("approval_actor") != "system"
                or not candidate.get("mapping_execution_id")
                or not candidate.get("evidence_fact_ids")
            ):
                findings.append(_finding("unsupported_derivation", "blocker", target_id, "System-approved derivation lacks model trace or evidence fact links.", source="deterministic"))
            if derivation.get("operation") == "date_diff_years" and not derivation.get("as_of_date"):
                findings.append(_finding("missing_as_of_date", "blocker", target_id, "Date-dependent derivation has no as-of date.", source="deterministic"))
        constraints = field.get("constraints") or {}
        expected_period = constraints.get("reporting_period")
        if expected_period and candidate.get("period_context") != expected_period:
            findings.append(_finding("reporting_period_mismatch", "blocker", target_id, f"Expected reporting period {expected_period!r}; selected evidence uses {candidate.get('period_context')!r}.", source="deterministic"))
    for group in payload.get("repeating_groups", []):
        if group.get("overflow_entity_ids"):
            findings.append(_finding("repeating_overflow", "blocker", group.get("id"), "Repeating records exceed target capacity.", source="deterministic"))
    return {"version": DETERMINISTIC_VERSION, "status": "fail" if any(item["severity"] == "blocker" for item in findings) else "pass", "findings": findings}
