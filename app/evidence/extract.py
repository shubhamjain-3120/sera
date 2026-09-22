import hashlib
import html
import re
from collections import defaultdict
from typing import Any

EXTRACTOR_VERSION = "rules-evidence-v1"
LABEL_VALUE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /_.()'-]{1,80}?)\s*[:=]\s*(.*?)\s*$")
FIELD_MARKER = re.compile(r"\b([A-Za-z][A-Za-z0-9 /_.()'-]{1,60}?):\s*")
ID_CARD_VALUE = re.compile(
    r"^\s*(DL|EXP|LN|FN|DOB|SEX|HAIR|EYES|HGT|WGT|ISS|CLASS|ENDORSEMENTS|RESTRICTIONS)\s+(.+?)\s*$",
    re.IGNORECASE,
)
EXPLICIT_ABSENCE = re.compile(r"\b(?:no|none|n/?a|not applicable)\b", re.IGNORECASE)
DIRECTION = re.compile(r"\b(?:please|kindly|must|should|complete|fill|use|submit|attach)\b", re.IGNORECASE)
DATE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
PERIOD = re.compile(r"\b(?:FY\s*\d{4}|Q[1-4]\s*\d{4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b", re.IGNORECASE)
UNIT = re.compile(r"(?:^|\s)(USD|CAD|EUR|GBP|kg|lb|miles?|years?|%)\b", re.IGNORECASE)


def _fact_id(entity_id: str, key: str, provenance: list[dict[str, Any]], raw: str) -> str:
    seed = f"{entity_id}|{key}|{provenance}|{raw}"
    return hashlib.sha256(seed.encode()).hexdigest()[:20]


def _location(
    block: dict[str, Any], artifact_id: str, label: str | None = None, raw_value: str | None = None
) -> dict[str, Any]:
    source = dict(block.get("source") or {})
    matching_words = _matching_ocr_words(block.get("ocr_words") or [], label or "", raw_value or "")
    if matching_words:
        padding = 0.003
        rects = [word["rect"] for word in matching_words]
        source["rect"] = [
            max(0.0, min(rect[0] for rect in rects) - padding),
            max(0.0, min(rect[1] for rect in rects) - padding),
            min(1.0, max(rect[2] for rect in rects) + padding),
            min(1.0, max(rect[3] for rect in rects) + padding),
        ]
        source["precision"] = "ocr_word_union"
    source["artifact_id"] = artifact_id
    source["excerpt"] = f"{label}: {raw_value}"[:240] if label else str(block.get("text") or "")[:240]
    return source


def _matching_ocr_words(words: list[dict[str, Any]], label: str, raw_value: str) -> list[dict[str, Any]]:
    target = re.sub(r"[^a-z0-9]", "", raw_value.casefold())
    label_target = re.sub(r"[^a-z0-9]", "", label.casefold())
    if len(target) < 2:
        return []
    normalized = []
    for word in words:
        rect = word.get("rect")
        token = re.sub(r"[^a-z0-9]", "", str(word.get("text") or "").casefold())
        if token and isinstance(rect, list) and len(rect) == 4:
            normalized.append((word, token, (rect[1] + rect[3]) / 2, rect[3] - rect[1]))
    normalized.sort(key=lambda item: (item[2], item[0]["rect"][0]))
    lines: list[list[tuple[dict[str, Any], str, float, float]]] = []
    for item in normalized:
        if not lines:
            lines.append([item])
            continue
        center = sum(member[2] for member in lines[-1]) / len(lines[-1])
        tolerance = max(0.008, item[3] * 0.75)
        if abs(item[2] - center) <= tolerance:
            lines[-1].append(item)
        else:
            lines.append([item])

    candidates = []
    for index, line in enumerate(lines):
        line.sort(key=lambda item: item[0]["rect"][0])
        line_text = "".join(item[1] for item in line)
        value_words = [item[0] for item in line if len(item[1]) >= 2 and item[1] in target]
        if not value_words:
            continue
        coverage = sum(len(item[1]) for item in line if len(item[1]) >= 2 and item[1] in target)
        has_label = bool(label_target and label_target in line_text)
        candidates.append((has_label, coverage, -index, index, value_words))
    if not candidates:
        return []
    _, _, _, line_index, selected = max(candidates, key=lambda item: item[:3])
    # Include an immediately following continuation line when it contains more
    # of the same value (for example, a two-line street address).
    if line_index + 1 < len(lines):
        current_bottom = max(item[0]["rect"][3] for item in lines[line_index])
        next_top = min(item[0]["rect"][1] for item in lines[line_index + 1])
        if next_top - current_bottom <= 0.025:
            selected.extend(
                item[0] for item in lines[line_index + 1] if len(item[1]) >= 2 and item[1] in target
            )
    return selected


def _canonical_key(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", ".", label.casefold()).strip(".")
    aliases = {
        "first.name": "person.given_name",
        "given.name": "person.given_name",
        "surname": "person.family_name",
        "last.name": "person.family_name",
        "date.of.birth": "person.date_of_birth",
        "dob": "person.date_of_birth",
        "driver.license": "driver.license_number",
        "driver.licence": "driver.license_number",
        "license.number": "driver.license_number",
        "licence.number": "driver.license_number",
        "license.no": "driver.license_number",
        "licence.no": "driver.license_number",
        "driver.name": "person.full_name",
        "vin.number": "vehicle.vin",
        "year": "vehicle.year",
        "make": "vehicle.make",
        "model": "vehicle.model",
        "value": "vehicle.value",
        "mileage": "vehicle.mileage",
        "vin": "vehicle.vin",
        "fn": "person.given_name",
        "ln": "person.family_name",
        "dl": "driver.license_number",
        "exp": "driver.license_expiration",
        "iss": "driver.license_issue_date",
    }
    return aliases.get(key, key)


def _entity_role(label: str, context: str) -> tuple[str, str]:
    combined = f"{label} {context}".casefold()
    if label.strip().casefold() in {"to", "from", "cc", "bcc", "subject", "sent", "date"}:
        return "email-metadata", "email_metadata"
    if "quoted" in combined or context.lstrip().startswith(">"):
        return "quoted-email", "quoted_email"
    if "broker" in combined or "producer" in combined:
        return "broker", "broker"
    if "vehicle" in combined or " vin" in f" {combined}":
        return "vehicle", "vehicle"
    return "applicant", "applicant"


def _candidate_pairs(blocks: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]], str]]:
    candidates: list[tuple[str, str, list[dict[str, Any]], str]] = []
    for index, block in enumerate(blocks):
        raw_text = str(block.get("text") or "")
        if str(block.get("type", "")).casefold() == "table":
            for label, value in _table_pairs(raw_text):
                candidates.append((label, value, [block], raw_text))
            continue
        text = _plain_text(raw_text).strip()
        pairs = _line_pairs(text)
        if not pairs:
            pairs = _bold_pairs(raw_text)
        if pairs:
            for label, value in pairs:
                candidates.append((label, value, [block], text))
            label, value = pairs[0]
            sources = [block]
            if len(pairs) == 1 and value.endswith("-") and index + 1 < len(blocks):
                continuation = str(blocks[index + 1].get("text") or "").strip()
                if re.fullmatch(r"[A-Za-z0-9]{2,20}", continuation) and re.search(
                    r"(?:licen[cs]e|identifier|\bid\b|vin|policy)", label, re.IGNORECASE
                ):
                    value += continuation
                    sources.append(blocks[index + 1])
                    candidates[-1] = (label, value, sources, text)
            continue
        source = block.get("source") or {}
        if source.get("kind") != "xlsx_range" or index + 1 >= len(blocks):
            continue
        next_block = blocks[index + 1]
        next_source = next_block.get("source") or {}
        if next_source.get("kind") != "xlsx_range" or source.get("sheet") != next_source.get("sheet"):
            continue
        left = re.fullmatch(r"([A-Z]+)(\d+)", str(source.get("cell_range", "")))
        right = re.fullmatch(r"([A-Z]+)(\d+)", str(next_source.get("cell_range", "")))
        if left and right and left.group(2) == right.group(2) and _column(right.group(1)) == _column(left.group(1)) + 1:
            value = str(next_block.get("text") or "").strip()
            if text and value and not text.startswith("="):
                candidates.append((text, value, [block, next_block], f"{text}: {value}"))
    return candidates


def _plain_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"</?[^>]+>", "", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    return html.unescape(value)


def _bold_pairs(value: str) -> list[tuple[str, str]]:
    markers = list(re.finditer(r"<b>(.*?)</b>\s*:?[ \t]*", value, flags=re.IGNORECASE | re.DOTALL))
    pairs = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(value)
        cleaned = _plain_text(value[marker.end() : end]).strip()
        if cleaned:
            pairs.append((_plain_text(marker.group(1)).strip(), cleaned))
    return pairs


def _table_pairs(value: str) -> list[tuple[str, str]]:
    """Convert Reducto markdown tables into header/value evidence pairs."""
    lines = [line.strip() for line in value.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return []
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    if not all(re.fullmatch(r"\s*:?-+:?\s*", cell) for cell in rows[1]):
        return []

    def clean_header(cell: str) -> str:
        cell = re.sub(r"<br\s*/?>", "", cell, flags=re.IGNORECASE)
        cleaned = _plain_text(cell).replace("\n", " ").strip()
        return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)

    def clean_value(cell: str) -> str:
        cell = re.sub(r"<br\s*/?>", "", cell, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", _plain_text(cell)).strip()

    headers = [clean_header(cell) for cell in rows[0]]
    pairs = []
    for row in rows[2:]:
        for header, cell in zip(headers, row, strict=False):
            cleaned = clean_value(cell)
            if header and cleaned:
                pairs.append((header, cleaned))
    return pairs


def _line_pairs(text: str) -> list[tuple[str, str]]:
    markers = [match for match in FIELD_MARKER.finditer(text) if match.group(1).casefold() not in {"http", "https"}]
    pairs = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        value = text[marker.end() : end].strip()
        if value:
            pairs.append((marker.group(1).strip(), value))
    if pairs:
        return pairs
    match = LABEL_VALUE.match(text) or ID_CARD_VALUE.match(text)
    if match and match.group(2).strip() and match.group(1).casefold() not in {"http", "https"}:
        return [(match.group(1).strip(), match.group(2).strip())]
    return []


def _column(letters: str) -> int:
    result = 0
    for letter in letters:
        result = result * 26 + ord(letter) - 64
    return result


def extract_evidence(
    blocks: list[dict[str, Any]], artifact_id: str, source_sha256: str, parser_provider: str, parser_version: str
) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    unreadable = []
    warnings: list[str] = []
    for block in blocks:
        if block.get("type") == "unreadable":
            location = _location(block, artifact_id)
            unreadable.append(
                {
                    "id": hashlib.sha256(str(location).encode()).hexdigest()[:20],
                    "reason": block.get("reason") or "Unreadable source region",
                    "provenance": location,
                    "confidence": 1.0,
                }
            )

    for label, raw_value, source_blocks, context in _candidate_pairs(blocks):
        key = _canonical_key(label)
        entity_id, role = _entity_role(label, context)
        provenance = [_location(block, artifact_id, label, raw_value) for block in source_blocks]
        uncertainty: list[str] = []
        semantics: list[str] = []
        accepted = True
        value: Any = raw_value
        ocr_confidences = [
            float(block["ocr_confidence"])
            for block in source_blocks
            if block.get("ocr_confidence") is not None
        ]
        extraction_confidence = min(ocr_confidences, default=0.98)
        if extraction_confidence < 0.75:
            uncertainty.append("Low OCR confidence; verify against the highlighted source region")
            accepted = False
        normalized_label = label.casefold()
        if key == "person.given_name" and EXPLICIT_ABSENCE.search(raw_value):
            value = None
            semantics.append("explicit_absence")
        if len(source_blocks) > 1 and raw_value.endswith(tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")):
            semantics.append("wrapped_identifier")
            raw_value = "\n".join(str(item.get("text") or "") for item in source_blocks)
        if "signature" in normalized_label:
            role = "broker" if "broker" in normalized_label else role
            entity_id = role
            semantics.append("signature_marker")
            accepted = False
            uncertainty.append("Signature presence is not applicant data")
        if role in {"quoted_email"}:
            semantics.append("quoted_content")
            accepted = False
            uncertainty.append("Quoted email content is not authoritative applicant data")
        if role == "email_metadata":
            semantics.append("email_metadata")
            accepted = False
            uncertainty.append("Email routing metadata is not applicant information")
        if DIRECTION.search(raw_value) or DIRECTION.search(label):
            role = "proposed_case_direction"
            entity_id = "case-directions"
            semantics.append("proposed_case_direction")
            accepted = False
            uncertainty.append("Untrusted document text; requires human approval before becoming a case direction")
        unit_match = UNIT.search(str(value)) if value is not None else None
        date_match = DATE.search(str(value)) if value is not None else None
        period_match = PERIOD.search(str(value)) if value is not None else None
        facts.append(
            {
                "id": _fact_id(entity_id, key, provenance, raw_value),
                "key": key,
                "label": label.strip(),
                "value": value,
                "raw_value": raw_value,
                "value_type": "date" if date_match else "text",
                "entity_id": entity_id,
                "entity_role": role,
                "provenance": provenance,
                "confidence": extraction_confidence if not uncertainty else min(extraction_confidence, 0.72),
                "uncertainty": uncertainty,
                "unit": unit_match.group(1) if unit_match else None,
                "date_context": date_match.group(0) if date_match else None,
                "period_context": period_match.group(0) if period_match else None,
                "duplicate_of": None,
                "contradicts": [],
                "accepted": accepted,
                "semantics": semantics,
            }
        )

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        grouped[(fact["entity_id"], fact["key"])].append(fact)
    for group in grouped.values():
        first_by_value: dict[str, dict[str, Any]] = {}
        for fact in group:
            fingerprint = str(fact["value"]).casefold().strip()
            if fingerprint in first_by_value:
                fact["duplicate_of"] = first_by_value[fingerprint]["id"]
            else:
                first_by_value[fingerprint] = fact
        unique = list(first_by_value.values())
        if len(unique) > 1:
            ids = [fact["id"] for fact in unique]
            for fact in unique:
                fact["contradicts"] = [identifier for identifier in ids if identifier != fact["id"]]
                fact["uncertainty"].append("Conflicting values found for the same entity and fact")
                fact["accepted"] = False
            warnings.append(f"Contradiction detected for {unique[0]['key']}")

    entities = []
    for entity_id, role in sorted({(fact["entity_id"], fact["entity_role"]) for fact in facts}):
        entities.append({"id": entity_id, "role": role})
    if unreadable:
        warnings.append(f"{len(unreadable)} unreadable region(s) require review")
    return {
        "facts": facts,
        "entities": entities,
        "unreadable_regions": unreadable,
        "parse_blocks": blocks,
        "parser_provider": parser_provider,
        "parser_version": parser_version,
        "extractor_version": EXTRACTOR_VERSION,
        "warnings": warnings,
        "source_sha256": source_sha256,
    }
