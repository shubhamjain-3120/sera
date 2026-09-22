import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

MAPPER_VERSION = "deterministic-mapper-v1"
ROLE_WORDS = {"applicant", "business", "driver", "vehicle", "broker", "owner"}
TOKEN_RE = re.compile(r"[a-z0-9]+")


class DerivationError(ValueError):
    pass


def _tokens(value: str) -> set[str]:
    return set(TOKEN_RE.findall(value.casefold().replace("_", " ")))


def _target_role(field: dict[str, Any]) -> str | None:
    words = _tokens(f"{field.get('semantic_type') or ''} {field.get('label') or ''}")
    return next((role for role in ROLE_WORDS if role in words), None)


def _type_compatible(field_type: str, fact: dict[str, Any]) -> bool:
    if fact.get("value") is None:
        return field_type in {"text", "unknown"}
    value_type = fact.get("value_type", "text")
    if field_type in {"text", "unknown", "choice"}:
        return True
    if field_type == "number":
        return value_type in {"number", "integer", "currency", "percent"} or _decimal(fact["value"]) is not None
    if field_type == "date":
        return value_type == "date" or _date_value(fact["value"]) is not None
    if field_type == "boolean":
        return str(fact["value"]).strip().casefold() in {"true", "false", "yes", "no", "y", "n", "1", "0"}
    return False


def _match_score(field: dict[str, Any], fact: dict[str, Any]) -> float:
    semantic = (field.get("semantic_type") or "").casefold()
    key = str(fact.get("key") or "").casefold()
    label = str(field.get("label") or "")
    fact_label = str(fact.get("label") or key)
    target_role = _target_role(field)
    fact_role = str(fact.get("entity_role") or "").casefold()
    if target_role and fact_role and target_role != fact_role:
        return 0.0
    if semantic and semantic == key:
        base = 1.0
    elif semantic and semantic.split(".")[-1] == key.split(".")[-1]:
        base = 0.88
    else:
        left = _tokens(f"{semantic} {label}") - ROLE_WORDS
        right = _tokens(f"{key} {fact_label}") - ROLE_WORDS
        overlap = len(left & right) / max(1, len(left | right))
        similarity = SequenceMatcher(None, label.casefold(), fact_label.casefold()).ratio()
        base = max(overlap, similarity * 0.82)
    if target_role and fact_role == target_role:
        base = min(1.0, base + 0.05)
    return round(base, 4)


def _decimal(value: Any) -> Decimal | None:
    try:
        cleaned = str(value).strip()
        negative = cleaned.startswith("(") and cleaned.endswith(")")
        if negative:
            cleaned = cleaned[1:-1].strip()
        cleaned = re.sub(r"^[\$€£]\s*", "", cleaned)
        cleaned = cleaned.removesuffix("%").strip().replace(",", "")
        if not re.fullmatch(r"-?(?:\d+(?:\.\d+)?|\.\d+)", cleaned):
            return None
        number = Decimal(cleaned)
        return -number if negative else number
    except InvalidOperation:
        return None


def _date_value(value: Any) -> date | None:
    try:
        raw = str(value).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return None
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _normalize(field: dict[str, Any], value: Any) -> tuple[Any, str, list[str]]:
    field_type = field.get("field_type", "unknown")
    if value is None:
        return None, "direct", []
    if field_type == "number":
        number = _decimal(value)
        if number is None:
            return value, "direct", ["Value could not be normalized as a number"]
        normalized: int | float = int(number) if number == number.to_integral() else float(number)
        return normalized, "normalized" if str(normalized) != str(value) else "direct", []
    if field_type == "date":
        parsed = _date_value(value)
        if parsed is None:
            return value, "direct", ["Value could not be normalized as an ISO date"]
        normalized = parsed.isoformat()
        return normalized, "normalized" if normalized != str(value) else "direct", []
    if field_type == "boolean":
        lowered = str(value).strip().casefold()
        normalized = lowered in {"true", "yes", "y", "1"}
        return normalized, "normalized", []
    if field_type == "choice" and field.get("options"):
        matches = [option for option in field["options"] if str(option).casefold() == str(value).strip().casefold()]
        if not matches:
            return value, "direct", ["Value is not an exact allowed choice"]
        return matches[0], "normalized" if matches[0] != value else "direct", []
    return value, "direct", []


def _candidate(field: dict[str, Any], snapshot_id: str, fact: dict[str, Any], score: float) -> dict[str, Any]:
    value, resolution, normalization_issues = _normalize(field, fact.get("value"))
    candidate_id = hashlib.sha1(f"{field['id']}:{snapshot_id}:{fact['id']}".encode()).hexdigest()[:20]
    uncertainty = list(fact.get("uncertainty", [])) + normalization_issues
    contradictions = list(fact.get("contradicts", []))
    selectable = bool(fact.get("accepted")) and not uncertainty and not contradictions and score >= 0.62
    return {
        "id": candidate_id,
        "target_field_id": field["id"],
        "snapshot_id": snapshot_id,
        "source_artifact_id": (fact.get("provenance") or [{}])[0].get("artifact_id"),
        "fact_id": fact["id"],
        "fact_key": fact.get("key"),
        "entity_id": fact.get("entity_id"),
        "entity_role": fact.get("entity_role"),
        "value": value,
        "raw_value": fact.get("raw_value"),
        "value_type": fact.get("value_type", "text"),
        "unit": fact.get("unit"),
        "date_context": fact.get("date_context"),
        "period_context": fact.get("period_context"),
        "origin": "evidence",
        "resolution": resolution,
        "evidence_confidence": fact.get("confidence", 0),
        "match_score": score,
        "provenance": fact.get("provenance", []),
        "uncertainty": uncertainty,
        "contradicts": contradictions,
        "selectable": selectable,
        "review_required": True,
    }


def build_fill_plan(
    template_schema: dict[str, Any],
    snapshots: list[tuple[str, dict[str, Any]]],
    candidate_facts: dict[str, list[tuple[str, dict[str, Any]]]] | None = None,
) -> dict[str, Any]:
    facts = [(snapshot_id, fact) for snapshot_id, snapshot in snapshots for fact in snapshot.get("facts", [])]
    targets: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    all_candidates: dict[str, dict[str, Any]] = {}
    for field in template_schema.get("fields", []):
        field_issues: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        writable = bool(field.get("writable", True)) and field.get("field_type") not in {"signature", "action"}
        if writable:
            for snapshot_id, fact in (candidate_facts or {}).get(field["id"], facts):
                if not _type_compatible(field.get("field_type", "unknown"), fact):
                    continue
                score = _match_score(field, fact)
                if score >= 0.34:
                    candidate = _candidate(field, snapshot_id, fact, score)
                    candidates.append(candidate)
                    all_candidates[candidate["id"]] = candidate
            candidates.sort(key=lambda item: (item["selectable"], item["match_score"], item["evidence_confidence"]), reverse=True)
        else:
            field_issues.append(_issue("forbidden_target", "info", field["id"], "Target is not an approved writable business field"))
        selected_id: str | None = None
        selectable = [candidate for candidate in candidates if candidate["selectable"]]
        if selectable:
            winner = selectable[0]
            competing_values = {
                json.dumps(item["value"], sort_keys=True, default=str)
                for item in selectable
                if item["match_score"] >= winner["match_score"] - 0.08
            }
            if len(competing_values) == 1:
                selected_id = winner["id"]
            else:
                field_issues.append(_issue("ambiguous_candidates", "review", field["id"], "Multiple similarly matched facts have different values"))
        if writable and not candidates:
            severity = "blocker" if field.get("required") else "review"
            field_issues.append(_issue("missing_evidence", severity, field["id"], "No grounded evidence candidate was found"))
        elif writable and candidates and selected_id is None and not field_issues:
            field_issues.append(_issue("uncertain_mapping", "review", field["id"], "Candidates exist but none can be selected without review"))
        if field.get("current_value") is not None and field.get("current_value") != "":
            field_issues.append(_issue("prefilled_disposition_required", "review", field["id"], "Existing target content requires an explicit later review decision"))
        issues.extend(field_issues)
        targets.append({
            "field": field,
            "selected_candidate_id": selected_id,
            "candidates": candidates,
            "issues": field_issues,
            "state": "proposed" if selected_id else ("not_applicable" if not writable else "unresolved"),
        })
    repeating = _repeating_results(template_schema, targets)
    for group in repeating:
        if group["overflow_entity_ids"]:
            issues.append(_issue("repeating_overflow", "blocker", group["id"], f"{len(group['overflow_entity_ids'])} record(s) exceed target capacity; all records were retained"))
    return {
        "mapper_version": MAPPER_VERSION,
        "template_schema": template_schema,
        "targets": targets,
        "repeating_groups": repeating,
        "derivations": [],
        "issues": issues,
        "summary": _summary(targets, issues),
    }


def _issue(code: str, severity: str, target_id: str, message: str) -> dict[str, Any]:
    return {"id": hashlib.sha1(f"{code}:{target_id}:{message}".encode()).hexdigest()[:16], "code": code, "severity": severity, "target_id": target_id, "message": message}


def _summary(targets: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "target_count": len(targets),
        "proposed_count": sum(target["selected_candidate_id"] is not None for target in targets),
        "unresolved_count": sum(target["state"] == "unresolved" for target in targets),
        "issue_count": len(issues),
        "blocker_count": sum(issue["severity"] == "blocker" for issue in issues),
    }


def _repeating_results(schema: dict[str, Any], targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_field = {target["field"]["id"]: target for target in targets}
    results = []
    for group in schema.get("repeating_groups", []):
        entity_ids = sorted({candidate["entity_id"] for field_id in group.get("field_ids", []) for candidate in by_field.get(field_id, {}).get("candidates", []) if candidate.get("entity_id")})
        capacity = int(group.get("capacity", 1))
        results.append({**group, "entity_ids": entity_ids, "assigned_entity_ids": entity_ids[:capacity], "overflow_entity_ids": entity_ids[capacity:]})
    return results


def apply_revision(payload: dict[str, Any], selections: dict[str, str | None], derivations: list[dict[str, Any]]) -> dict[str, Any]:
    result = json.loads(json.dumps(payload))
    target_by_id = {target["field"]["id"]: target for target in result["targets"]}
    candidate_by_id = {candidate["id"]: candidate for target in result["targets"] for candidate in target["candidates"]}
    for field_id, candidate_id in selections.items():
        target = target_by_id.get(field_id)
        if target is None:
            raise ValueError(f"Unknown target field {field_id}")
        if candidate_id is not None:
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None or candidate["target_field_id"] != field_id:
                raise ValueError(f"Candidate {candidate_id} does not belong to target {field_id}")
            if not candidate["selectable"]:
                raise ValueError(f"Candidate {candidate_id} is uncertain and cannot be selected silently")
        target["selected_candidate_id"] = candidate_id
        target["state"] = "proposed" if candidate_id else "unresolved"
    derived, derivation_issues = evaluate_derivations(derivations, candidate_by_id)
    for candidate in derived:
        target = target_by_id.get(candidate["target_field_id"])
        if target is None:
            raise ValueError(f"Unknown derivation target {candidate['target_field_id']}")
        target["candidates"].append(candidate)
    result["derivations"] = derivations
    base_issues = [issue for issue in result["issues"] if issue["code"] not in {"invalid_derivation", "derivation_depth", "derivation_cycle"}]
    result["issues"] = base_issues + derivation_issues
    result["summary"] = _summary(result["targets"], result["issues"])
    return result


def evaluate_derivations(specs: list[dict[str, Any]], base_candidates: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {spec["id"]: spec for spec in specs}
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(derivation_id: str) -> int:
        if derivation_id in depths:
            return depths[derivation_id]
        if derivation_id in visiting:
            raise DerivationError("cycle")
        visiting.add(derivation_id)
        spec = by_id[derivation_id]
        value = 1 + max((depth(input_id) for input_id in spec["input_ids"] if input_id in by_id), default=0)
        visiting.remove(derivation_id)
        depths[derivation_id] = value
        return value

    issues: list[dict[str, Any]] = []
    values = {candidate_id: candidate["value"] for candidate_id, candidate in base_candidates.items()}
    provenance = {candidate_id: candidate["provenance"] for candidate_id, candidate in base_candidates.items()}
    output: list[dict[str, Any]] = []
    pending = set(by_id)
    try:
        for derivation_id in by_id:
            if depth(derivation_id) > 2:
                issues.append(_issue("derivation_depth", "blocker", by_id[derivation_id]["target_field_id"], "Derivation depth exceeds the maximum of two"))
                pending.discard(derivation_id)
    except DerivationError:
        for derivation_id in pending:
            issues.append(_issue("derivation_cycle", "blocker", by_id[derivation_id]["target_field_id"], "Cyclic derivation was rejected"))
        return [], issues
    for _ in range(2):
        for derivation_id in list(pending):
            spec = by_id[derivation_id]
            if any(input_id not in values for input_id in spec["input_ids"]):
                continue
            try:
                value = _derive(spec, [values[input_id] for input_id in spec["input_ids"]])
            except (DerivationError, InvalidOperation, ZeroDivisionError) as exc:
                issues.append(_issue("invalid_derivation", "blocker", spec["target_field_id"], str(exc)))
                pending.remove(derivation_id)
                continue
            locations = [item for input_id in spec["input_ids"] for item in provenance.get(input_id, [])]
            candidate = {
                "id": derivation_id,
                "target_field_id": spec["target_field_id"],
                "snapshot_id": None,
                "source_artifact_id": None,
                "fact_id": None,
                "fact_key": None,
                "entity_id": None,
                "entity_role": None,
                "value": value,
                "raw_value": str(value),
                "value_type": "derived",
                "unit": None,
                "date_context": None,
                "period_context": None,
                "origin": "derivation",
                "resolution": "derived",
                "evidence_confidence": min((base_candidates[item]["evidence_confidence"] for item in spec["input_ids"] if item in base_candidates), default=1),
                "match_score": 1,
                "provenance": locations,
                "uncertainty": [],
                "contradicts": [],
                "selectable": False,
                "review_required": True,
                "derivation": {**spec, "depth": depths[derivation_id]},
            }
            output.append(candidate)
            values[derivation_id] = value
            provenance[derivation_id] = locations
            pending.remove(derivation_id)
    for derivation_id in pending:
        issues.append(_issue("invalid_derivation", "blocker", by_id[derivation_id]["target_field_id"], "Derivation input is missing or rejected"))
    return output, issues


def _derive(spec: dict[str, Any], values: list[Any]) -> Any:
    operation = spec["operation"]
    if operation == "concat":
        return spec.get("separator", " ").join(str(value) for value in values)
    if operation == "date_diff_years":
        if len(values) != 1 or not spec.get("as_of_date"):
            raise DerivationError("Date-dependent derivation requires one date and an explicit as-of date")
        start = _date_value(values[0])
        end = _date_value(spec["as_of_date"])
        if not start or not end:
            raise DerivationError("Invalid derivation date")
        return end.year - start.year - ((end.month, end.day) < (start.month, start.day))
    numbers = [_decimal(value) for value in values]
    if any(value is None for value in numbers):
        raise DerivationError("Numeric derivation received a non-numeric input")
    operands = [value for value in numbers if value is not None]
    if operation == "sum":
        result = sum(operands, Decimal(0))
    elif operation == "subtract" and len(operands) == 2:
        result = operands[0] - operands[1]
    elif operation == "multiply":
        result = Decimal(1)
        for operand in operands:
            result *= operand
    elif operation == "divide" and len(operands) == 2:
        result = operands[0] / operands[1]
    else:
        raise DerivationError(f"Unsupported operands for {operation}")
    return int(result) if result == result.to_integral() else float(result)
