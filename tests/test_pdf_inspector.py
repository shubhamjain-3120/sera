import io

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

from app.inspectors.pdf import inspect_pdf


def pdf_with_widgets() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_annotation(
        0,
        {
            "/Type": "/Annot",
            "/Subtype": "/Widget",
            "/FT": "/Tx",
            "/T": "Applicant name",
            "/Rect": [72, 700, 250, 720],
            "/Ff": 2,
        },
    )
    writer.add_annotation(
        0,
        {
            "/Type": "/Annot",
            "/Subtype": "/Widget",
            "/FT": "/Sig",
            "/T": "Broker signature",
            "/Rect": [72, 100, 250, 125],
        },
    )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_inspection_keeps_widget_geometry_and_excludes_signature_writes():
    result = inspect_pdf(io.BytesIO(pdf_with_widgets()))
    assert result["inspection"]["page_count"] == 1
    assert result["inspection"]["widget_count"] == 2
    text, signature = result["fields"]
    assert text["location"]["rect"] == [72.0, 700.0, 250.0, 720.0]
    assert text["location"]["page_width"] == 612.0
    assert text["location"]["page_height"] == 792.0
    assert text["required"] is True
    assert signature["field_type"] == "signature"
    assert signature["writable"] is False


def test_flat_and_rotated_pdf_fixtures_are_explicit():
    flat_writer = PdfWriter()
    flat_writer.add_blank_page(width=612, height=792)
    flat = io.BytesIO()
    flat_writer.write(flat)
    flat_result = inspect_pdf(io.BytesIO(flat.getvalue()))
    assert flat_result["fields"] == []
    assert "No native widgets" in flat_result["inspection"]["warnings"][0]

    rotated_writer = PdfWriter()
    rotated_writer.add_blank_page(width=612, height=792).rotate(90)
    rotated_writer.add_annotation(
        0,
        {
            "/Type": "/Annot",
            "/Subtype": "/Widget",
            "/FT": "/Tx",
            "/T": "Rotated field",
            "/Rect": [10, 20, 100, 40],
        },
    )
    rotated = io.BytesIO()
    rotated_writer.write(rotated)
    result = inspect_pdf(io.BytesIO(rotated.getvalue()))
    assert result["fields"][0]["location"]["rotation"] == 90


def test_widgets_with_the_same_parent_are_one_logical_field():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    parent = DictionaryObject(
        {
            NameObject("/FT"): NameObject("/Btn"),
            NameObject("/T"): TextStringObject("Has safety program"),
            NameObject("/Kids"): ArrayObject(),
        }
    )
    parent_reference = writer._add_object(parent)
    button_states = ("/Yes", "/No")
    for rect, state in zip(([10, 10, 20, 20], [30, 10, 40, 20]), button_states):
        appearance = DictionaryObject(
            {
                NameObject("/Off"): DictionaryObject(),
                NameObject(state): DictionaryObject(),
            }
        )
        annotation = writer.add_annotation(
            0,
            {
                "/Type": "/Annot",
                "/Subtype": "/Widget",
                "/Rect": rect,
                "/Parent": parent_reference,
                "/AP": DictionaryObject({NameObject("/N"): appearance}),
            },
        )
        parent["/Kids"].append(annotation.indirect_reference)
    output = io.BytesIO()
    writer.write(output)

    result = inspect_pdf(io.BytesIO(output.getvalue()))
    assert result["inspection"]["widget_count"] == 2
    assert result["inspection"]["logical_field_count"] == 1
    assert len(result["fields"]) == 1
    assert result["fields"][0]["widget_count"] == 2
    assert len(result["fields"][0]["widgets"]) == 2
    assert result["fields"][0]["constraints"]["button_states"] == ["/No", "/Yes"]
    assert [option["export_value"] for option in result["fields"][0]["widget_options"]] == [
        "/Yes",
        "/No",
    ]


def test_terminal_widget_siblings_under_structural_parent_remain_distinct():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    structural_parent = DictionaryObject(
        {
            NameObject("/T"): TextStringObject("category-row"),
            NameObject("/Kids"): ArrayObject(),
        }
    )
    parent_reference = writer._add_object(structural_parent)
    for name, rect in (("0", [10, 10, 20, 20]), ("1", [30, 10, 40, 20])):
        annotation = writer.add_annotation(
            0,
            {
                "/Type": "/Annot",
                "/Subtype": "/Widget",
                "/FT": "/Tx",
                "/T": name,
                "/Rect": rect,
                "/Parent": parent_reference,
            },
        )
        structural_parent["/Kids"].append(annotation.indirect_reference)
    output = io.BytesIO()
    writer.write(output)

    result = inspect_pdf(io.BytesIO(output.getvalue()))
    assert result["inspection"]["widget_count"] == 2
    assert result["inspection"]["logical_field_count"] == 2
    assert [field["native_name"] for field in result["fields"]] == ["0", "1"]
    assert all(field["widget_count"] == 1 for field in result["fields"])


def test_full_field_name_preserves_parent_path_and_terminal_identity():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    root = DictionaryObject(
        {NameObject("/T"): TextStringObject("Col4"), NameObject("/Kids"): ArrayObject()}
    )
    root_ref = writer._add_object(root)
    middle = DictionaryObject(
        {
            NameObject("/T"): TextStringObject("16"),
            NameObject("/Parent"): root_ref,
            NameObject("/Kids"): ArrayObject(),
        }
    )
    middle_ref = writer._add_object(middle)
    annotation = writer.add_annotation(
        0,
        {
            "/Type": "/Annot",
            "/Subtype": "/Widget",
            "/FT": "/Tx",
            "/T": "1",
            "/Parent": middle_ref,
            "/Rect": [10, 10, 20, 20],
        },
    )
    middle["/Kids"].append(annotation.indirect_reference)
    root["/Kids"].append(middle_ref)
    output = io.BytesIO()
    writer.write(output)

    field = inspect_pdf(io.BytesIO(output.getvalue()))["fields"][0]
    assert field["native_name"] == "1"
    assert field["native_full_name"] == "Col4.16.1"
    assert field["native_object_id"].startswith("widget:")


def test_choice_export_display_values_and_pdf_write_constraints_are_preserved():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_annotation(
        0,
        {
            "/Type": "/Annot",
            "/Subtype": "/Widget",
            "/FT": "/Ch",
            "/T": "Limit",
            "/Opt": ArrayObject(
                [
                    ArrayObject([TextStringObject("300000"), TextStringObject("300,000")]),
                    TextStringObject("NONE"),
                ]
            ),
            "/MaxLen": 12,
            "/Rect": [10, 10, 20, 20],
        },
    )
    output = io.BytesIO()
    writer.write(output)

    field = inspect_pdf(io.BytesIO(output.getvalue()))["fields"][0]
    assert field["choice_options"] == [
        {"export_value": "300000", "display_value": "300,000"},
        {"export_value": "NONE", "display_value": "NONE"},
    ]
    assert field["options"] == ["300,000", "NONE"]
    assert field["constraints"]["max_length"] == 12
