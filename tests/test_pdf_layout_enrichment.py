from app.inspectors.layout import LayoutBlock, _clean_question, enrich_fields


def field(name: str, kind: str, rect: list[float]) -> dict:
    return {
        "label": name,
        "native_name": name,
        "field_type": kind,
        "location": {
            "kind": "pdf_rect",
            "page": 1,
            "rect": rect,
            "page_height": 792,
            "rotation": 0,
        },
    }


def test_checkbox_label_combines_grounded_question_and_option():
    target = field("1", "boolean", [27, 527, 35, 535])
    blocks = [
        LayoutBlock(1, "Do driver hiring practices include the following (check all that apply):", 20, 243, 281, 253),
        LayoutBlock(1, "Written application", 38, 257, 107, 266),
    ]
    counts = enrich_fields([target], blocks)
    assert target["label"] == "Do driver hiring practices include the following - Written application"
    assert target["native_name"] == "1"
    assert target["review_state"] == "needs_review"
    assert target["label_confidence"] == 0.96
    assert [item["relation"] for item in target["label_evidence"]] == [
        "question_context",
        "option_label",
    ]
    assert counts["high"] == 1


def test_text_field_uses_nearby_left_label_and_grounded_semantic_normalization():
    target = field("1", "text", [180, 690, 300, 710])
    blocks = [LayoutBlock(1, "Applicant name:", 70, 85, 170, 103)]
    enrich_fields([target], blocks)
    assert target["label"] == "Applicant name:"
    assert target["semantic_type"] == "applicant.legal_name"
    assert target["semantic_type_origin"] == "rule"
    assert target["label_evidence"][0]["source"] == "native-text"


def test_unresolved_field_keeps_native_identity_and_low_confidence():
    target = field("1", "boolean", [10, 10, 18, 18])
    counts = enrich_fields([target], [])
    assert target["label"] == "1"
    assert target["label_origin"] == "native"
    assert target["label_confidence"] == 0.25
    assert "semantic_type" not in target
    assert counts["unresolved"] == 1


def test_question_cleanup_discards_adjacent_yes_no_text():
    assert (
        _clean_question("No Does applicant allow others to operate under their authority?")
        == "Does applicant allow others to operate under their authority?"
    )
