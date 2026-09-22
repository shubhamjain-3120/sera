import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pdfplumber


@dataclass(frozen=True)
class LayoutBlock:
    page: int
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    source: str = "native-text"

    def as_evidence(self, relation: str) -> dict[str, Any]:
        return {
            "text": self.text,
            "page": self.page,
            "rect": [self.x0, self.top, self.x1, self.bottom],
            "coordinate_system": "pdf-top-left",
            "source": self.source,
            "relation": relation,
        }


def extract_pdf_layout(content: bytes) -> list[LayoutBlock]:
    """Extract bounded text segments without treating document text as instructions."""
    blocks: list[LayoutBlock] = []
    with pdfplumber.open(BytesIO(content)) as document:
        for page_number, page in enumerate(document.pages, start=1):
            words = page.extract_words(
                x_tolerance=2,
                y_tolerance=2,
                keep_blank_chars=False,
                use_text_flow=False,
            )
            blocks.extend(_words_to_segments(page_number, words))
    return blocks


def canonical_blocks_to_layout(blocks: list[dict[str, Any]]) -> list[LayoutBlock]:
    """Accept provider blocks only when they contain explicit page rectangles."""
    result: list[LayoutBlock] = []
    for block in blocks:
        source = block.get("source") or {}
        rect = source.get("rect")
        page = source.get("page")
        text = str(block.get("text") or "").strip()
        if not text or not page or not isinstance(rect, list) or len(rect) != 4:
            continue
        coordinate_system = source.get("coordinate_system", "provider")
        if coordinate_system not in {"pdf-top-left", "top-left", "provider-top-left"}:
            continue
        result.append(
            LayoutBlock(
                page=int(page),
                text=text,
                x0=float(rect[0]),
                top=float(rect[1]),
                x1=float(rect[2]),
                bottom=float(rect[3]),
                source="reducto",
            )
        )
    return result


def _words_to_segments(page: int, words: list[dict[str, Any]]) -> list[LayoutBlock]:
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        if not rows or abs(float(word["top"]) - _row_top(rows[-1])) > 2.5:
            rows.append([word])
        else:
            rows[-1].append(word)
    segments: list[LayoutBlock] = []
    for row in rows:
        current: list[dict[str, Any]] = []
        for word in sorted(row, key=lambda item: float(item["x0"])):
            gap = float(word["x0"]) - float(current[-1]["x1"]) if current else 0
            if current and gap > 5:
                segments.append(_segment(page, current))
                current = []
            current.append(word)
        if current:
            segments.append(_segment(page, current))
    return segments


def _row_top(row: list[dict[str, Any]]) -> float:
    return sum(float(word["top"]) for word in row) / len(row)


def _segment(page: int, words: list[dict[str, Any]]) -> LayoutBlock:
    return LayoutBlock(
        page=page,
        text=" ".join(str(word["text"]) for word in words).strip(),
        x0=min(float(word["x0"]) for word in words),
        top=min(float(word["top"]) for word in words),
        x1=max(float(word["x1"]) for word in words),
        bottom=max(float(word["bottom"]) for word in words),
    )


def enrich_fields(
    fields: list[dict[str, Any]],
    native_blocks: list[LayoutBlock],
    provider_blocks: list[LayoutBlock] | None = None,
) -> dict[str, int]:
    blocks = list(provider_blocks or []) + native_blocks
    by_page: dict[int, list[LayoutBlock]] = {}
    for block in blocks:
        by_page.setdefault(block.page, []).append(block)
    counts = {"high": 0, "medium": 0, "low": 0, "unresolved": 0}
    for field in fields:
        proposals = []
        for index, location in enumerate(field.get("widgets") or [field["location"]]):
            located_field = {**field, "location": location}
            proposal = _propose_label(
                located_field, by_page.get(location["page"], [])
            )
            if proposal:
                option_evidence = next(
                    (
                        item
                        for item in proposal[2]
                        if item["relation"] == "option_label"
                    ),
                    None,
                )
                if option_evidence and index < len(field.get("widget_options", [])):
                    option = field["widget_options"][index]
                    visible_label = option_evidence["text"]
                    export_value = str(option.get("export_value") or "")
                    if export_value.lower() in {"yes", "no"} and visible_label.lower().startswith(
                        export_value.lower()
                    ):
                        visible_label = export_value
                    option["label"] = visible_label
                proposals.append(proposal)
        for option in field.get("widget_options", []):
            if not option.get("label") and str(option.get("export_value", "")).lower() in {
                "yes",
                "no",
            }:
                option["label"] = option["export_value"]
        proposal = max(proposals, key=lambda item: item[1], default=None)
        if proposal and field.get("widget_count", 1) > 1:
            question_evidence = [
                item for item in proposal[2] if item["relation"] == "question_context"
            ]
            if question_evidence:
                proposal = (
                    _clean_question(question_evidence[0]["text"]),
                    proposal[1],
                    question_evidence,
                )
        native_label = _humanize_native_name(field["label"])
        native_is_descriptive = not _generic_name(field["label"])
        protected_kind = field["field_type"] in {"signature", "action"}
        if native_is_descriptive and (
            proposal is None
            or proposal[1] < 0.9
            or protected_kind
            or field["field_type"] != "boolean"
        ):
            field["label"] = native_label
            field["label_origin"] = "native"
            field["label_confidence"] = 0.8 if protected_kind else 0.7
            field["review_state"] = "needs_review"
            field["label_evidence"] = []
            _assign_semantic_type(field)
            counts["medium"] += 1
            continue
        if proposal is None:
            field["label_origin"] = "native"
            field["label_confidence"] = 0.25 if _generic_name(field["label"]) else 0.55
            field["review_state"] = "needs_review"
            field["label_evidence"] = []
            counts["unresolved"] += 1
            continue
        label, confidence, evidence = proposal
        field["label"] = label
        field["label_origin"] = "layout"
        field["label_confidence"] = confidence
        field["review_state"] = "needs_review"
        field["label_evidence"] = evidence
        _assign_semantic_type(field)
        bucket = "high" if confidence >= 0.9 else "medium" if confidence >= 0.7 else "low"
        counts[bucket] += 1
    return counts


def _propose_label(
    field: dict[str, Any], blocks: list[LayoutBlock]
) -> tuple[str, float, list[dict[str, Any]]] | None:
    rect = field["location"].get("rect")
    height = float(field["location"].get("page_height") or 792)
    rotation = int(field["location"].get("rotation") or 0) % 360
    if not rect or rotation not in {0, 180}:
        return None
    x0, y0, x1, y1 = (float(value) for value in rect)
    top, bottom = height - y1, height - y0
    center = (top + bottom) / 2
    clean_blocks = [block for block in blocks if _useful(block.text)]

    inside = [
        block
        for block in clean_blocks
        if x0 <= (block.x0 + block.x1) / 2 <= x1
        and top <= (block.top + block.bottom) / 2 <= bottom
    ]

    right = [
        block
        for block in clean_blocks
        if _vertical_distance(center, block) <= 5 and -3 <= block.x0 - x1 <= 26
    ]
    left = [
        block
        for block in clean_blocks
        if _vertical_distance(center, block) <= 5 and 0 <= x0 - block.x1 <= 190
    ]
    above = [
        block
        for block in clean_blocks
        if 0 <= top - block.bottom <= 38 and _horizontal_overlap(x0, x1, block) > 0
    ]
    option = min(right, key=lambda block: block.x0 - x1, default=None)
    left_label = min(left, key=lambda block: x0 - block.x1, default=None)
    above_label = min(above, key=lambda block: top - block.bottom, default=None)

    if field["field_type"] in {"boolean", "choice"} and option:
        if str(field.get("native_name", "")).lower().startswith("coverage_") and left_label:
            return _truncate(left_label.text), 0.94, [
                left_label.as_evidence("row_label")
            ]
        question = _nearest_question(clean_blocks, option, center, x0)
        if question and question.text != option.text:
            label = f"{_clean_question(question.text)} - {option.text}"
            return _truncate(label), 0.96, [
                question.as_evidence("question_context"),
                option.as_evidence("option_label"),
            ]
        return _truncate(option.text), 0.92, [option.as_evidence("option_label")]
    # Some generated forms place a terminal text widget over its visible,
    # fixed category caption. That caption is stronger evidence than the
    # neighboring cell to the left.
    if field["field_type"] == "text" and inside:
        embedded_label = min(
            inside,
            key=lambda block: abs((block.top + block.bottom) / 2 - center),
        )
        return _truncate(embedded_label.text), 0.94, [
            embedded_label.as_evidence("field_text")
        ]
    if left_label:
        return _truncate(left_label.text), 0.9, [left_label.as_evidence("left_label")]
    if above_label:
        return _truncate(above_label.text), 0.78, [above_label.as_evidence("above_label")]
    if option:
        return _truncate(option.text), 0.72, [option.as_evidence("right_label")]
    return None


def _nearest_question(
    blocks: list[LayoutBlock], option: LayoutBlock, field_center: float, field_x0: float
) -> LayoutBlock | None:
    same_row_candidates = []
    above_candidates = []
    for block in blocks:
        normalized = block.text.strip().lower()
        if normalized in {"yes", "yes?", "no", "no?"}:
            continue
        looks_like_prompt = any(
            marker in block.text.lower()
            for marker in ("?", ":", "check all")
        )
        if not looks_like_prompt or block is option:
            continue
        same_row_left = (
            # Wrapped checklist headings can sit on the second text baseline
            # while their first option remains on the first baseline.
            abs((block.top + block.bottom) / 2 - field_center) <= 10
            and block.x1 <= field_x0
        )
        above = 0 <= option.top - block.bottom <= 75
        if same_row_left:
            same_row_candidates.append((field_x0 - block.x1, block))
        elif above:
            above_candidates.append((option.top - block.bottom, block))
    if same_row_candidates:
        checklist_prompts = [
            item for item in same_row_candidates if "check all" in item[1].text.lower()
        ]
        if checklist_prompts:
            return min(checklist_prompts, key=lambda item: item[0])[1]
        return min(same_row_candidates, key=lambda item: item[0])[1]
    checklist_prompts = [
        item for item in above_candidates if "check all" in item[1].text.lower()
    ]
    if checklist_prompts:
        return min(checklist_prompts, key=lambda item: item[0])[1]
    return min(above_candidates, key=lambda item: item[0], default=(0, None))[1]


def _horizontal_overlap(x0: float, x1: float, block: LayoutBlock) -> float:
    return max(0, min(x1, block.x1) - max(x0, block.x0))


def _vertical_distance(center: float, block: LayoutBlock) -> float:
    if block.top <= center <= block.bottom:
        return 0
    return min(abs(center - block.top), abs(center - block.bottom))


def _clean_question(text: str) -> str:
    cleaned = re.sub(
        r"\s*\(check all that apply\)\s*:?", "", text, flags=re.I
    ).rstrip(" :")
    return re.sub(r"^(?:yes|no)\s+(?=.+\?)", "", cleaned, flags=re.I)


def _truncate(text: str, length: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= length else compact[: length - 1].rstrip() + "…"


def _generic_name(name: str) -> bool:
    compact = name.strip().lower()
    return bool(re.fullmatch(r"(?:field|widget)?[_ -]*\d+", compact)) or compact in {
        "yes",
        "no",
        "on",
        "off",
    }


def _humanize_native_name(name: str) -> str:
    value = re.sub(r"^(?:rivington|acroform)[_ -]+", "", name, flags=re.I)
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or name


def _useful(text: str) -> bool:
    return bool(text.strip()) and not bool(re.fullmatch(r"[_\W]+", text.strip()))


def _assign_semantic_type(field: dict[str, Any]) -> None:
    if field.get("semantic_type") or _generic_name(field["label"]):
        return
    normalized = re.sub(r"[^a-z0-9]+", "_", field["label"].lower()).strip("_")
    if not normalized:
        return
    mappings = (
        (r"^(?:insured|applicant)_name$", "applicant.legal_name"),
        (r"^effective_date$", "policy.effective_date"),
        (r"^dot_number$", "applicant.usdot_number"),
        (r"^mc_number$", "applicant.mc_number"),
        (r"^fein$", "applicant.fein"),
        (r"^mailing_address$", "applicant.mailing_address.line1"),
        (r"^mailing_city$", "applicant.mailing_address.city"),
        (r"^mailing_state$", "applicant.mailing_address.state"),
        (r"^mailing_zip$", "applicant.mailing_address.postal_code"),
        (r"^garaging_address$", "applicant.garaging_address.line1"),
        (r"^garaging_city$", "applicant.garaging_address.city"),
        (r"^garaging_state$", "applicant.garaging_address.state"),
        (r"^garaging_zip$", "applicant.garaging_address.postal_code"),
    )
    semantic_type = next(
        (value for pattern, value in mappings if re.match(pattern, normalized)),
        f"template.{normalized[:100]}",
    )
    field["semantic_type"] = semantic_type
    field["semantic_type_origin"] = "rule"
    field["semantic_type_confidence"] = field.get("label_confidence")
