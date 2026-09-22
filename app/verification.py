import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

DETERMINISTIC_VERSION = "deterministic-validation-v1"
VERIFIER_VERSION = "independent-evidence-verifier-v1"


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
            if not derivation.get("input_ids") or not candidate.get("review_required"):
                findings.append(_finding("unsupported_derivation", "blocker", target_id, "Derivation lacks grounded inputs or mandatory review.", source="deterministic"))
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


class IndependentVerifier(Protocol):
    provider: str
    model: str | None
    version: str

    def verify(self, payload: dict[str, Any], snapshots: list[dict[str, Any]]) -> dict[str, Any]: ...


@dataclass
class EvidenceAwareVerifier:
    """A separate semantic pass with no write path to the Fill Plan."""

    provider: str = "local-independent"
    model: str | None = None
    version: str = VERIFIER_VERSION

    def verify(self, payload: dict[str, Any], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        started = time.perf_counter()
        facts = [fact for snapshot in snapshots for fact in snapshot.get("facts", []) if fact.get("accepted")]
        fact_by_id = {fact.get("id"): fact for fact in facts}
        findings: list[dict[str, Any]] = []
        additional_evidence: list[dict[str, Any]] = []
        for target in payload.get("targets", []):
            field = target.get("field", {})
            target_id = field.get("id")
            selected = _selected(target)
            if target.get("review", {}).get("origin") == "human":
                # Human decisions are terminal semantic authority. Deterministic
                # validation still runs, but the independent verifier must not
                # reinterpret or overturn the reviewer.
                continue
            target_role = next((role for role in ("applicant", "business", "driver", "vehicle", "broker", "owner") if role in _tokens(f"{field.get('semantic_type')} {field.get('label')}")), None)
            if selected:
                fact = fact_by_id.get(selected.get("fact_id"))
                if selected.get("origin") == "evidence" and fact is None:
                    findings.append(_finding("evidence_not_in_snapshot", "blocker", target_id, "Selected evidence is absent from the frozen evidence bundle.", source="verifier"))
                if fact:
                    if target_role and fact.get("entity_role") and target_role != str(fact.get("entity_role")).casefold():
                        findings.append(_finding("incorrect_entity", "blocker", target_id, f"Target expects {target_role} evidence but the selection belongs to {fact.get('entity_role')}.", source="verifier", evidence=fact.get("provenance")))
                    if fact.get("value") != selected.get("value") and selected.get("resolution") == "direct":
                        findings.append(_finding("value_not_supported", "blocker", target_id, "Direct selected value does not equal its cited evidence fact.", source="verifier", evidence=fact.get("provenance")))
            semantic = str(field.get("semantic_type") or "").casefold()
            label_tokens = _tokens(field.get("label"))
            plausible = []
            for fact in facts:
                fact_key = str(fact.get("key") or "").casefold()
                role_ok = not target_role or not fact.get("entity_role") or target_role == str(fact.get("entity_role")).casefold()
                semantic_ok = bool(semantic and (semantic == fact_key or semantic.split(".")[-1] == fact_key.split(".")[-1]))
                token_ok = bool(label_tokens and label_tokens & _tokens(f"{fact_key} {fact.get('label')}"))
                if role_ok and (semantic_ok or token_ok):
                    plausible.append(fact)
            selected_fact_id = selected.get("fact_id") if selected else None
            missed = [fact for fact in plausible if fact.get("id") != selected_fact_id]
            if selected is None and missed:
                best = max(missed, key=lambda item: item.get("confidence", 0))
                evidence = {"target_id": target_id, "snapshot_fact_id": best.get("id"), "value": best.get("value"), "confidence": best.get("confidence"), "provenance": best.get("provenance", [])}
                additional_evidence.append(evidence)
                findings.append(_finding("missed_evidence", "review", target_id, "The verifier found plausible evidence that the mapper did not select.", source="verifier", evidence=best.get("provenance")))
            elif selected and any(fact.get("value") != selected.get("value") for fact in missed):
                findings.append(_finding("conflicting_evidence", "review", target_id, "Other frozen evidence supports a different value.", source="verifier", evidence=[location for fact in missed for location in fact.get("provenance", [])]))
        status = "fail" if any(item["severity"] == "blocker" for item in findings) else "needs_review" if findings else "pass"
        return {"version": self.version, "status": status, "findings": findings, "additional_evidence": additional_evidence, "duration_ms": round((time.perf_counter() - started) * 1000, 2)}


def build_verification_report(payload: dict[str, Any], snapshots: list[dict[str, Any]], verifier: IndependentVerifier | None = None) -> dict[str, Any]:
    """Run checks in order and prove the verifier did not mutate selections."""
    verifier = verifier or EvidenceAwareVerifier()
    before = json.dumps([(target.get("field", {}).get("id"), target.get("selected_candidate_id")) for target in payload.get("targets", [])], sort_keys=True)
    deterministic = deterministic_validate(payload)
    independent = verifier.verify(json.loads(json.dumps(payload)), json.loads(json.dumps(snapshots)))
    after = json.dumps([(target.get("field", {}).get("id"), target.get("selected_candidate_id")) for target in payload.get("targets", [])], sort_keys=True)
    if before != after:
        raise RuntimeError("Verifier mutated selected values")
    statuses = {deterministic["status"], independent["status"]}
    status = "fail" if "fail" in statuses else "needs_review" if "needs_review" in statuses else "pass"
    return {"status": status, "deterministic": deterministic, "independent": independent, "selection_hash_before": hashlib.sha256(before.encode()).hexdigest(), "selection_hash_after": hashlib.sha256(after.encode()).hexdigest(), "selection_unchanged": True}
