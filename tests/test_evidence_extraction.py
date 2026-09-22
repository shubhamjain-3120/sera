import io
import json
from pathlib import Path

from openpyxl import Workbook

from app.evidence.extract import extract_evidence
from app.evidence.parse import native_parse
from app.parsers.recorded import RecordedParserAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "reducto_license.json"


def test_recorded_scanned_license_preserves_absence_wrapping_and_unreadable_regions():
    response = json.loads(FIXTURE.read_text())
    parsed = RecordedParserAdapter(response).poll("fixture")
    snapshot = extract_evidence(parsed.blocks, "artifact-1", "a" * 64, "recorded", parsed.parser_version)

    given_name = next(fact for fact in snapshot["facts"] if fact["key"] == "person.given_name")
    assert given_name["value"] is None
    assert given_name["accepted"] is True
    assert "explicit_absence" in given_name["semantics"]
    assert given_name["provenance"][0]["rect"] == [44, 68, 280, 98]

    license_number = next(fact for fact in snapshot["facts"] if fact["key"] == "driver.license_number")
    assert license_number["value"] == "D123-4567"
    assert len(license_number["provenance"]) == 2
    assert "wrapped_identifier" in license_number["semantics"]
    assert snapshot["unreadable_regions"][0]["reason"] == "Glare obscures address line 2"


def test_duplicates_contradictions_roles_and_untrusted_directions_are_explicit():
    def block(text: str, start: int) -> dict:
        return {
            "type": "text",
            "text": text,
            "source": {"kind": "text_span", "char_start": start, "char_end": start + len(text)},
        }

    blocks = [
        block("DOB: 2020-01-02", 0),
        block("DOB: 2020-01-02", 20),
        block("DOB: 2021-02-03", 40),
        block("Broker signature: Signed", 60),
        block("Quoted email: Please use the old address", 90),
    ]
    snapshot = extract_evidence(blocks, "artifact-2", "b" * 64, "recorded", "recorded-v1")
    dob = [fact for fact in snapshot["facts"] if fact["key"] == "person.date_of_birth"]
    assert dob[1]["duplicate_of"] == dob[0]["id"]
    assert dob[0]["contradicts"] == [dob[2]["id"]]
    assert dob[0]["accepted"] is False
    signature = next(fact for fact in snapshot["facts"] if "signature_marker" in fact["semantics"])
    assert signature["entity_role"] == "broker"
    assert signature["accepted"] is False
    direction = next(fact for fact in snapshot["facts"] if "proposed_case_direction" in fact["semantics"])
    assert direction["entity_role"] == "proposed_case_direction"
    assert direction["accepted"] is False


def test_native_workbook_parse_keeps_multiple_sheets_and_exact_cells():
    workbook = Workbook()
    applicant = workbook.active
    applicant.title = "Applicant"
    applicant.append(["Given name", "Amina"])
    vehicles = workbook.create_sheet("Vehicles")
    vehicles.append(["VIN", "1ABC234"])
    stream = io.BytesIO()
    workbook.save(stream)

    parsed = native_parse("xlsx", stream.getvalue())
    locations = [block["source"] for block in parsed.blocks]
    assert {location["sheet"] for location in locations} == {"Applicant", "Vehicles"}
    assert {location["cell_range"] for location in locations} == {"A1", "B1"}


def test_native_image_marks_unreadable_instead_of_guessing():
    from PIL import Image

    image = Image.new("RGB", (120, 80), "white")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    parsed = native_parse("image", stream.getvalue())
    assert parsed.blocks[0]["type"] == "unreadable"
    assert parsed.blocks[0]["source"]["rect"] == [0.0, 0.0, 120.0, 80.0]


def test_reducto_markdown_table_preserves_wrapped_identifiers():
    blocks = [
        {
            "type": "table",
            "text": (
                "| Driver<br />Name | Licence<br />No. | Date of<br />Birth |\n"
                "|-|-|-|\n"
                "| moham<br />mad | U2054<br />044 | 01-26-<br />1992 |"
            ),
            "source": {"kind": "pdf_rect", "page": 2, "rect": [0.1, 0.2, 0.8, 0.4]},
            "ocr_confidence": 0.98,
        }
    ]
    snapshot = extract_evidence(blocks, "artifact-1", "sha", "reducto", "reducto-v1")
    values = {fact["key"]: fact["value"] for fact in snapshot["facts"]}
    assert values["person.full_name"] == "mohammad"
    assert values["driver.license_number"] == "U2054044"
    assert values["person.date_of_birth"] == "01-26-1992"


def test_reducto_ocr_words_narrow_fact_provenance():
    blocks = [
        {
            "type": "key_value",
            "text": "Name: Mohammad Khalifeh\nPhone: 555-1234",
            "source": {
                "kind": "pdf_rect",
                "page": 1,
                "rect": [0.1, 0.1, 0.9, 0.8],
                "coordinate_system": "reducto-normalized-top-left",
            },
            "ocr_words": [
                {"text": "Mohammad", "rect": [0.3, 0.2, 0.4, 0.23], "confidence": 0.99},
                {"text": "Khalifeh", "rect": [0.41, 0.2, 0.5, 0.23], "confidence": 0.99},
                {"text": "555-1234", "rect": [0.3, 0.5, 0.4, 0.53], "confidence": 0.99},
            ],
            "ocr_confidence": 0.98,
        }
    ]
    snapshot = extract_evidence(blocks, "artifact-1", "sha", "reducto", "reducto-v1")
    name = next(fact for fact in snapshot["facts"] if fact["key"] == "name")
    assert name["provenance"][0]["rect"] == [0.297, 0.197, 0.503, 0.233]
    assert name["provenance"][0]["precision"] == "ocr_word_union"
