"""Write reviewed values into a copy of the published PDF or workbook."""

from __future__ import annotations

import copy
import io
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree as ET

from openpyxl.utils.datetime import to_excel
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from app.xlsx_preserve import MAIN_NS, _sheet_part


class FormWriteError(RuntimeError):
    def __init__(self, field_id: str, message: str):
        self.field_id = field_id
        self.message = message
        super().__init__(message)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _pdf_value(field: dict[str, Any], value: Any) -> str:
    if _is_blank(value):
        if field.get("field_type") == "boolean":
            return "/Off"
        blank_option = next(
            (option.get("export_value") for option in field.get("choice_options", [])
             if _is_blank(option.get("display_value"))),
            None,
        )
        return str(blank_option) if blank_option is not None else ""
    if field.get("field_type") == "boolean" and isinstance(value, bool):
        if not value:
            return "/Off"
        enabled = next(
            (option.get("export_value") for option in field.get("widget_options", [])
             if option.get("export_value") not in (None, "", "/Off")),
            "/Yes",
        )
        return str(enabled)
    if field.get("field_type") == "choice":
        option = next(
            (option for option in field.get("choice_options", [])
             if str(option.get("display_value")) == str(value)),
            None,
        )
        if option:
            return str(option["export_value"])
    return str(value)


def _write_pdf(original: bytes, fields: list[dict[str, Any]], values: dict[str, Any]) -> bytes:
    writer = PdfWriter()
    writer.clone_document_from_reader(PdfReader(io.BytesIO(original)))
    overlays: dict[int, list[tuple[dict[str, Any], Any]]] = defaultdict(list)
    changed = False
    for field in fields:
        if field.get("writable") is False or field.get("field_type") in {"action", "signature"}:
            continue
        field_id = str(field["id"])
        if field_id not in values:
            continue
        value = values[field_id]
        previous = field.get("current_value")
        if value == previous or (_is_blank(value) and _is_blank(previous)):
            continue
        changed = True
        name = field.get("native_full_name") or field.get("native_name")
        if not name:
            if not _is_blank(value):
                location = field.get("location") or {}
                try:
                    page_index = int(location["page"]) - 1
                    if page_index < 0 or page_index >= len(writer.pages):
                        raise ValueError("Page is outside the PDF")
                    rect = location["rect"]
                    if len(rect) != 4:
                        raise ValueError("Field rectangle is invalid")
                except (KeyError, TypeError, ValueError) as exc:
                    raise FormWriteError(field_id, f"Could not locate flat PDF field: {exc}") from exc
                overlays[page_index].append((field, value))
            continue
        try:
            writer.update_page_form_field_values(
                None, {str(name): _pdf_value(field, value)}, auto_regenerate=False
            )
        except Exception as exc:
            raise FormWriteError(field_id, f"Could not write PDF field: {exc}") from exc
    if not changed:
        return original
    for page_index, entries in overlays.items():
        page = writer.pages[page_index]
        overlay_bytes = io.BytesIO()
        drawing = canvas.Canvas(
            overlay_bytes, pagesize=(float(page.mediabox.width), float(page.mediabox.height))
        )
        for field, value in entries:
            field_id = str(field["id"])
            try:
                x1, y1, x2, y2 = (float(item) for item in field["location"]["rect"])
                text = "X" if field.get("field_type") == "boolean" and value is True else str(value)
                size = min(11.0, max(6.0, (y2 - y1) * 0.7))
                drawing.setFont("Helvetica", size)
                drawing.drawString(x1 + 2, y1 + max(1.0, (y2 - y1 - size) / 2), text)
            except Exception as exc:
                raise FormWriteError(field_id, f"Could not draw flat PDF field: {exc}") from exc
        drawing.save()
        overlay_bytes.seek(0)
        try:
            page.merge_page(PdfReader(overlay_bytes).pages[0])
        except Exception as exc:
            raise FormWriteError(str(entries[0][0]["id"]), f"Could not merge PDF overlay: {exc}") from exc
    output = io.BytesIO()
    try:
        writer.write(output)
    except Exception as exc:
        raise FormWriteError("", f"Could not save PDF: {exc}") from exc
    return output.getvalue()


def _cell_coordinate(cell_range: str) -> tuple[str, int]:
    match = re.match(r"\$?([A-Z]+)\$?(\d+)", cell_range.upper())
    if not match:
        raise ValueError(f"Unsupported cell location: {cell_range}")
    return f"{match.group(1)}{match.group(2)}", int(match.group(2))


def _cell(root: ET.Element, coordinate: str, row_number: int) -> ET.Element:
    sheet_data = root.find(f"{{{MAIN_NS}}}sheetData")
    if sheet_data is None:
        raise ValueError("Worksheet has no sheet data")
    row = sheet_data.find(f"{{{MAIN_NS}}}row[@r='{row_number}']")
    if row is None:
        row = ET.Element(f"{{{MAIN_NS}}}row", {"r": str(row_number)})
        existing_rows = list(sheet_data)
        index = next(
            (index for index, item in enumerate(existing_rows)
             if item.tag == f"{{{MAIN_NS}}}row" and int(item.get("r", "0")) > row_number),
            len(existing_rows),
        )
        sheet_data.insert(index, row)
    cell = row.find(f"{{{MAIN_NS}}}c[@r='{coordinate}']")
    if cell is None:
        cell = ET.SubElement(row, f"{{{MAIN_NS}}}c", {"r": coordinate})
    return cell


def _set_cell_value(cell: ET.Element, field: dict[str, Any], value: Any) -> None:
    if cell.find(f"{{{MAIN_NS}}}f") is not None:
        raise ValueError("Cannot write into a formula cell")
    for child in list(cell):
        if child.tag in {f"{{{MAIN_NS}}}v", f"{{{MAIN_NS}}}is"}:
            cell.remove(child)
    if _is_blank(value):
        cell.attrib.pop("t", None)
    elif field.get("field_type") == "date":
        if isinstance(value, (date, datetime)):
            parsed = value
        else:
            parsed = None
            for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"):
                try:
                    parsed = datetime.strptime(str(value).strip(), pattern)
                    break
                except ValueError:
                    continue
        if parsed is None:
            raise ValueError(f"Unsupported date: {value}")
        cell.attrib.pop("t", None)
        ET.SubElement(cell, f"{{{MAIN_NS}}}v").text = str(to_excel(parsed))
    elif isinstance(value, bool):
        cell.set("t", "b")
        ET.SubElement(cell, f"{{{MAIN_NS}}}v").text = "1" if value else "0"
    elif isinstance(value, (int, float)):
        cell.attrib.pop("t", None)
        ET.SubElement(cell, f"{{{MAIN_NS}}}v").text = str(value)
    elif field.get("field_type") == "number" and re.fullmatch(
        r"[-+]?\d[\d,]*(?:\.\d+)?", str(value).strip()
    ):
        cell.attrib.pop("t", None)
        ET.SubElement(cell, f"{{{MAIN_NS}}}v").text = str(value).replace(",", "")
    else:
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
        ET.SubElement(inline, f"{{{MAIN_NS}}}t").text = str(value)


def _write_xlsx(original: bytes, fields: list[dict[str, Any]], values: dict[str, Any]) -> bytes:
    changes: dict[str, list[tuple[dict[str, Any], str, int, Any]]] = defaultdict(list)
    for field in fields:
        if field.get("writable") is False or field.get("field_type") in {"action", "signature"}:
            continue
        field_id = str(field["id"])
        if field_id not in values:
            continue
        value = values[field_id]
        previous = field.get("current_value")
        if value == previous or (_is_blank(value) and _is_blank(previous)):
            continue
        location = field.get("location") or {}
        try:
            sheet = str(location["sheet"])
            coordinate, row_number = _cell_coordinate(str(location["cell_range"]))
        except (KeyError, ValueError) as exc:
            raise FormWriteError(field_id, f"Could not locate workbook cell: {exc}") from exc
        changes[sheet].append((field, coordinate, row_number, value))
    if not changes:
        return original
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original), "r") as before:
        parts: dict[str, list[tuple[dict[str, Any], str, int, Any]]] = defaultdict(list)
        for sheet, edits in changes.items():
            try:
                parts[_sheet_part(before, sheet)].extend(edits)
            except Exception as exc:
                raise FormWriteError(str(edits[0][0]["id"]), f"Could not find worksheet: {exc}") from exc
        with zipfile.ZipFile(output, "w") as after:
            for info in before.infolist():
                data = before.read(info.filename)
                if info.filename in parts:
                    try:
                        root = ET.fromstring(data)
                    except Exception as exc:
                        raise FormWriteError("", f"Could not read worksheet: {exc}") from exc
                    for field, coordinate, row_number, value in parts[info.filename]:
                        try:
                            _set_cell_value(_cell(root, coordinate, row_number), field, value)
                        except Exception as exc:
                            raise FormWriteError(str(field["id"]), f"Could not write workbook cell: {exc}") from exc
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                after.writestr(copy.copy(info), data)
    return output.getvalue()


def write_form(
    original: bytes,
    template_schema: dict[str, Any],
    values: dict[str, Any],
    kind: str,
) -> bytes:
    """Return a filled copy; native failures carry the affected template field ID."""
    fields = list(template_schema.get("fields", []))
    if kind == "pdf":
        return _write_pdf(original, fields, values)
    if kind == "xlsx":
        return _write_xlsx(original, fields, values)
    raise FormWriteError("", f"Unsupported output type: {kind}")
