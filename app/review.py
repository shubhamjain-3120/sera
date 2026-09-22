import copy
import hashlib
import re
from datetime import date, datetime
from typing import Any

from app.mapping import format_write_value, validate_write_value

REVIEW_VERSION = "human-review-v1"
EXCEPTION_ACTIONS = {"clear", "not_applicable", "intentional_blank"}


def _canonical_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def selected_candidate(target: dict[str, Any]) -> dict[str, Any] | None:
    selected_id = target.get("selected_candidate_id")
    return next(
        (item for item in target.get("candidates", []) if item.get("id") == selected_id),
        None,
    )


def selected_value(target: dict[str, Any]) -> Any:
    selected = selected_candidate(target)
    return selected.get("value") if selected else None


def _human_candidate(
    target: dict[str, Any], decision_id: str, value: Any, action: str, prior: dict[str, Any] | None
) -> dict[str, Any]:
    field_id = target["field"]["id"]
    return {
        "id": f"human-{decision_id}",
        "target_field_id": field_id,
        "snapshot_id": prior.get("snapshot_id") if prior else None,
        "source_artifact_id": prior.get("source_artifact_id") if prior else None,
        "fact_id": prior.get("fact_id") if prior else None,
        "fact_key": prior.get("fact_key") if prior else None,
        "entity_id": prior.get("entity_id") if prior else None,
        "entity_role": prior.get("entity_role") if prior else None,
        "value": value,
        "raw_value": "" if value is None else str(value),
        "value_type": target["field"].get("field_type", "unknown"),
        "unit": prior.get("unit") if prior else None,
        "date_context": prior.get("date_context") if prior else None,
        "period_context": prior.get("period_context") if prior else None,
        "origin": "human",
        "resolution": "human",
        "evidence_confidence": prior.get("evidence_confidence", 1) if prior else 1,
        "match_score": prior.get("match_score", 1) if prior else 1,
        "provenance": copy.deepcopy(prior.get("provenance", [])) if prior else [],
        "uncertainty": [],
        "contradicts": [],
        "selectable": False,
        "review_required": False,
        "review_decision_id": decision_id,
        "prior_candidate_id": prior.get("id") if prior else None,
        "review_action": action,
        "derivation": copy.deepcopy(prior.get("derivation")) if prior and prior.get("derivation") else None,
    }


def _coerce_and_validate(field: dict[str, Any], value: Any) -> Any:
    field_type = field.get("field_type", "unknown")
    constraints = field.get("constraints") or {}
    if (field_type == "date" or constraints.get("format_hint") == "date") and value not in (None, ""):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Human edit for a date field must be an ISO date (YYYY-MM-DD)")
        from datetime import date

        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Human edit for a date field must be a valid ISO date") from exc
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Human edit for a number field must be numeric")
    elif field_type == "boolean" and not isinstance(value, bool):
        raise ValueError("Human edit for a boolean field must be true or false")
    elif field_type == "choice" and field.get("options") and value not in field["options"]:
        raise ValueError("Human edit is not an approved choice")
    elif field_type in {"signature", "action"}:
        raise ValueError("Signatures and actions cannot be reviewed as fill values")
    return value


def _issue(code: str, severity: str, target_id: str, message: str) -> dict[str, Any]:
    material = f"review:{code}:{target_id}:{message}"
    return {
        "id": hashlib.sha1(material.encode()).hexdigest()[:16],
        "code": code,
        "severity": severity,
        "target_id": target_id,
        "message": message,
    }


def _summarize(payload: dict[str, Any]) -> None:
    targets = payload.get("targets", [])
    issues = payload.get("issues", [])
    payload["summary"] = {
        "target_count": len(targets),
        "proposed_count": sum(target.get("selected_candidate_id") is not None for target in targets),
        "unresolved_count": sum(target.get("state") == "unresolved" for target in targets),
        "issue_count": len(issues),
        "blocker_count": sum(issue.get("severity") == "blocker" for issue in issues),
        "reviewed_count": sum(bool(target.get("review")) for target in targets),
        "review_pending_count": sum(
            target.get("state") in {"proposed", "unresolved", "dependency_changed"}
            for target in targets
            if target.get("field", {}).get("writable", True)
        ),
    }


def apply_review_decision(
    payload: dict[str, Any],
    *,
    decision_id: str,
    target_field_id: str,
    action: str,
    actor: str,
    reason: str | None,
    candidate_id: str | None,
    value: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = copy.deepcopy(payload)
    targets = {item["field"]["id"]: item for item in result.get("targets", [])}
    target = targets.get(target_field_id)
    if target is None:
        raise ValueError(f"Unknown target field {target_field_id}")
    field = target["field"]
    if not field.get("writable", True) or field.get("field_type") in {"signature", "action"}:
        raise ValueError("Technical integrity rules forbid review writes to this target")
    previous_candidate = selected_candidate(target)
    previous_value = selected_value(target)
    existing_review = target.get("review") or {}

    if action == "acknowledge_dependency":
        notice = existing_review.get("dependency_notice")
        if not notice or notice.get("acknowledged"):
            raise ValueError("This target has no unacknowledged dependency change")
        notice["acknowledged"] = True
        notice["acknowledged_by"] = actor
        notice["acknowledgement_reason"] = reason
        target["state"] = "reviewed"
        target["review"]["status"] = "approved"
        detail = {"dependency_acknowledged": True}
        _summarize(result)
        return result, {
            "previous_value": previous_value,
            "new_value": previous_value,
            "candidate_id": target.get("selected_candidate_id"),
            "detail": detail,
        }

    selected = None
    if action == "approve":
        selected = previous_candidate
        if selected is None:
            raise ValueError("There is no selected proposal to approve")
        value = selected.get("value")
        candidate_id = selected.get("id")
    elif action == "select_candidate":
        if not candidate_id:
            raise ValueError("candidate_id is required when selecting a candidate")
        selected = next(
            (item for item in target.get("candidates", []) if item.get("id") == candidate_id),
            None,
        )
        if selected is None:
            raise ValueError("Candidate does not belong to this target")
        value = selected.get("value")
    elif action == "edit":
        value = _coerce_and_validate(field, value)
    elif action == "retain_prefilled":
        value = field.get("current_value")
        if value is None or value == "":
            raise ValueError("This target has no prefilled value to retain")
        if (field.get("constraints") or {}).get("format_hint") == "date":
            canonical_date = _canonical_date(value)
            if canonical_date is None:
                raise ValueError("The prefilled date cannot be safely retained; edit it as an ISO date")
            value = canonical_date
    elif action == "clear":
        value = None
    elif action == "intentional_blank":
        value = ""
    elif action == "not_applicable":
        value = "N/A"
    else:
        raise ValueError(f"Unsupported review action {action}")

    required_exception = bool(field.get("required") and action in EXCEPTION_ACTIONS)
    if required_exception and not (reason or "").strip():
        raise ValueError("A reason is required for a required-field exception")

    prefilled = field.get("current_value") is not None and field.get("current_value") != ""
    if action == "retain_prefilled":
        prefilled_disposition = "retain"
    elif prefilled and action in {"clear", "intentional_blank", "not_applicable"}:
        prefilled_disposition = "clear"
    elif prefilled:
        prefilled_disposition = "replace"
    else:
        prefilled_disposition = None

    human_candidate = _human_candidate(target, decision_id, value, action, selected)
    human_candidate["canonical_value"] = value
    human_candidate["mapping_method"] = "human"
    human_candidate["approval_state"] = "human_approved"
    if action in EXCEPTION_ACTIONS:
        human_candidate["write_value"] = value
    elif action == "retain_prefilled" and (field.get("constraints") or {}).get("format_hint") == "date":
        # The stored PDF text is already in the target's native date format;
        # retain those exact bytes while exposing an ISO canonical value.
        human_candidate["write_value"] = field.get("current_value")
    else:
        format_field = dict(field)
        if (field.get("constraints") or {}).get("format_hint") == "date":
            format_field["field_type"] = "date"
        try:
            write_value = format_write_value(format_field, value)
        except ValueError as exc:
            raise ValueError(f"Human edit cannot be written to this target: {exc}") from exc
        issues = validate_write_value(
            format_field,
            value,
            write_value,
            allow_blank=action in {"clear", "intentional_blank"},
            allow_exception=action == "not_applicable",
        )
        if issues:
            raise ValueError("Human edit violates target constraints: " + "; ".join(issues))
        human_candidate["write_value"] = write_value
    target.setdefault("candidates", []).append(human_candidate)
    target["selected_candidate_id"] = human_candidate["id"]
    target["state"] = "reviewed"
    target["review"] = {
        "status": "exception" if required_exception else "approved",
        "origin": "human",
        "action": action,
        "actor": actor,
        "reason": reason,
        "decision_id": decision_id,
        "required_exception": required_exception,
        "prefilled_disposition": prefilled_disposition,
        "terminal_authority": True,
    }

    resolved_codes = {
        "missing_evidence",
        "uncertain_mapping",
        "ambiguous_candidates",
        "prefilled_disposition_required",
        "dependency_changed",
    }
    target["issues"] = [
        issue for issue in target.get("issues", []) if issue.get("code") not in resolved_codes
    ]
    result["issues"] = [
        issue
        for issue in result.get("issues", [])
        if not (issue.get("target_id") == target_field_id and issue.get("code") in resolved_codes)
    ]

    if previous_value != value:
        previous_id = previous_candidate.get("id") if previous_candidate else None
        for dependent in result.get("targets", []):
            if dependent is target:
                continue
            dependent_candidate = selected_candidate(dependent)
            inputs = (dependent_candidate or {}).get("derivation", {}).get("input_ids", [])
            if not previous_id or previous_id not in inputs:
                continue
            if dependent.get("review", {}).get("origin") == "human":
                dependent["review"]["dependency_notice"] = {
                    "changed_target_id": target_field_id,
                    "previous_input_candidate_id": previous_id,
                    "acknowledged": False,
                }
                dependent["review"]["status"] = "needs_acknowledgement"
                dependent["state"] = "dependency_changed"
            else:
                dependent["selected_candidate_id"] = None
                dependent["state"] = "unresolved"
            issue = _issue(
                "dependency_changed",
                "review",
                dependent["field"]["id"],
                f"An upstream reviewed value ({field.get('label')}) changed; review this dependent value.",
            )
            dependent.setdefault("issues", []).append(issue)
            result.setdefault("issues", []).append(issue)

    if result.get("template_schema", {}).get("inspection", {}).get("format") == "xlsx":
        result["preview_calculation"] = {
            "status": "stale",
            "changed_target_id": target_field_id,
            "message": "Dependent formula previews are stale; recalculation is deferred until a supported calculation adapter is available. No output file was generated.",
        }
    result["review_version"] = REVIEW_VERSION
    _summarize(result)
    return result, {
        "previous_value": previous_value,
        "new_value": value,
        "candidate_id": candidate_id,
        "detail": {
            "required_exception": required_exception,
            "prefilled_disposition": prefilled_disposition,
            "previous_candidate_id": previous_candidate.get("id") if previous_candidate else None,
            "human_candidate_id": human_candidate["id"],
        },
    }
