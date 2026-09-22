import io

from pypdf import PdfWriter

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
