import hashlib
import io
import re
import zipfile
from typing import Any, BinaryIO
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _field_id(sheet: str, cell_range: str) -> str:
    return hashlib.sha1(f"{sheet}!{cell_range}".encode()).hexdigest()[:16]


def _list_extended_validations(archive: zipfile.ZipFile) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for name in archive.namelist():
        if not name.startswith("xl/worksheets/sheet") or not name.endswith(".xml"):
            continue
        text = archive.read(name).decode("utf-8", errors="replace")
        pattern = r"<x14:dataValidation\b([^>]*)>(.*?)</x14:dataValidation>"
        for match in re.finditer(pattern, text, re.DOTALL):
            body = match.group(2)
            range_match = re.search(r"<xm:sqref>(.*?)</xm:sqref>", body, re.DOTALL)
            formula_match = re.search(r"<xm:f>(.*?)</xm:f>", body, re.DOTALL)
            type_match = re.search(r'\btype="([^"]+)"', match.group(1))
            if range_match:
                results.append(
                    {
                        "part": name,
                        "range": range_match.group(1),
                        "kind": "extended",
                        "type": type_match.group(1) if type_match else "custom",
                        "formula1": formula_match.group(1) if formula_match else "",
                    }
                )
    return results


def _package_inventory(archive: zipfile.ZipFile) -> dict[str, Any]:
    names = archive.namelist()
    return {
        "part_count": len(names),
        "table_parts": sorted(name for name in names if name.startswith("xl/tables/")),
        "external_links": sorted(name for name in names if name.startswith("xl/externalLinks/")),
        "has_macros": "xl/vbaProject.bin" in names,
    }


def _sheet_part_map(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relation.attrib["Id"]: relation.attrib["Target"].lstrip("/")
        for relation in relationships.findall(f".//{{{PKG_REL_NS}}}Relationship")
    }
    result = {}
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        relation_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
        target = targets.get(relation_id, "")
        part = target if target.startswith("xl/") else f"xl/{target}"
        result[part] = sheet.attrib["name"]
    return result


def inspect_xlsx(stream: BinaryIO) -> dict[str, Any]:
    data = stream.read()
    workbook = load_workbook(io.BytesIO(data), read_only=False, data_only=False, keep_links=True)
    fields: list[dict[str, Any]] = []
    sheets: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    warnings: list[str] = []

    for sheet in workbook.worksheets:
        sheets.append(
            {
                "name": sheet.title,
                "state": sheet.sheet_state,
                "protected": bool(sheet.protection.sheet),
                "max_row": sheet.max_row,
                "max_column": sheet.max_column,
                "merged_ranges": [str(item) for item in sheet.merged_cells.ranges],
                "tables": sorted(sheet.tables.keys()),
            }
        )
        for validation in sheet.data_validations.dataValidation:
            ranges = [str(item) for item in validation.ranges.ranges]
            validations.append(
                {
                    "sheet": sheet.title,
                    "ranges": ranges,
                    "type": validation.type,
                    "formula1": validation.formula1,
                    "formula2": validation.formula2,
                    "error": validation.error,
                }
            )
            for cell_range in ranges:
                key = f"{sheet.title}!{cell_range}"
                fields.append(
                    {
                        "id": _field_id(sheet.title, cell_range),
                        "label": key,
                        "field_type": "choice" if validation.type == "list" else "unknown",
                        "required": None,
                        "writable": not bool(sheet.protection.sheet),
                        "options": [],
                        "location": {"kind": "xlsx_range", "sheet": sheet.title, "cell_range": cell_range},
                        "notes": f"Validation: {validation.type or 'custom'}",
                    }
                )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        inventory = _package_inventory(archive)
        extended = _list_extended_validations(archive)
        sheet_by_part = _sheet_part_map(archive)
    for item in extended:
        sheet_name = sheet_by_part.get(item["part"], item["part"])
        item["sheet"] = sheet_name
        validations.append(
            {
                "sheet": sheet_name,
                "ranges": [item["range"]],
                "type": item["type"],
                "formula1": item["formula1"],
                "formula2": None,
                "source": "extended-x14",
            }
        )
        fields.append(
            {
                "id": _field_id(sheet_name, f"x14:{item['range']}"),
                "label": f"{sheet_name}!{item['range']}",
                "field_type": "choice" if item["type"] == "list" else "unknown",
                "required": None,
                "writable": True,
                "options": [],
                "location": {
                    "kind": "xlsx_range",
                    "sheet": sheet_name,
                    "cell_range": item["range"],
                },
                "notes": "Extended Excel validation (x14); preserved by OOXML writer",
            }
        )
        if "#REF!" in item["formula1"]:
            warnings.append(
                f"Broken extended validation reference at {sheet_name}!{item['range']}"
            )
    names = []
    for name, definition in workbook.defined_names.items():
        names.append({"name": name, "value": definition.attr_text, "hidden": bool(definition.hidden)})
    return {
        "fields": fields,
        "repeating_groups": [],
        "inspection": {
            "format": "xlsx",
            "sheets": sheets,
            "defined_names": names,
            "validations": validations,
            "extended_validations": extended,
            "package": inventory,
            "warnings": warnings,
        },
    }


def read_sheet_grid(stream: BinaryIO, sheet_name: str, min_row: int, max_row: int, min_col: int, max_col: int) -> dict[str, Any]:
    workbook = load_workbook(stream, read_only=False, data_only=False, keep_links=True)
    if sheet_name not in workbook.sheetnames:
        raise KeyError(sheet_name)
    sheet = workbook[sheet_name]
    rows = []
    for row in sheet.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
        rows.append(
            [
                {
                    "coordinate": cell.coordinate,
                    "value": cell.value,
                    "formula": isinstance(cell.value, str) and cell.value.startswith("="),
                    "locked": bool(cell.protection.locked),
                    "number_format": cell.number_format,
                    "fill": cell.fill.fgColor.rgb if cell.fill.fill_type else None,
                }
                for cell in row
            ]
        )
    return {"sheet": sheet_name, "bounds": [min_row, max_row, min_col, max_col], "rows": rows}
