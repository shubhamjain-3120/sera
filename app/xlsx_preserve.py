"""Minimal OOXML patcher used by the Phase 1 preservation feasibility spike.

It deliberately copies every package part byte-for-byte except the selected worksheet XML.
Full approved-write planning belongs to Phase 6.
"""

import copy
import io
import posixpath
import zipfile
from xml.etree import ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("", MAIN_NS)


def _sheet_part(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet = workbook.find(f".//{{{MAIN_NS}}}sheet[@name='{sheet_name}']")
    if sheet is None:
        raise KeyError(sheet_name)
    rel_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relation = relationships.find(f".//{{{PKG_REL_NS}}}Relationship[@Id='{rel_id}']")
    if relation is None:
        raise ValueError(f"Missing relationship for {sheet_name}")
    target = relation.attrib["Target"].lstrip("/")
    if target.startswith("xl/"):
        return posixpath.normpath(target)
    return posixpath.normpath(posixpath.join("xl", target))


def patch_cell(source: bytes, sheet_name: str, coordinate: str, value: str) -> bytes:
    """Change one non-formula cell while preserving all unrelated ZIP parts."""
    input_buffer = io.BytesIO(source)
    output_buffer = io.BytesIO()
    with zipfile.ZipFile(input_buffer, "r") as before:
        sheet_part = _sheet_part(before, sheet_name)
        with zipfile.ZipFile(output_buffer, "w") as after:
            for info in before.infolist():
                data = before.read(info.filename)
                if info.filename == sheet_part:
                    root = ET.fromstring(data)
                    cell = root.find(f".//{{{MAIN_NS}}}c[@r='{coordinate}']")
                    if cell is None:
                        raise KeyError(f"Cell {sheet_name}!{coordinate} does not exist")
                    if cell.find(f"{{{MAIN_NS}}}f") is not None:
                        raise ValueError("Formula replacement requires an explicit template edit")
                    for child in list(cell):
                        if child.tag in {f"{{{MAIN_NS}}}v", f"{{{MAIN_NS}}}is"}:
                            cell.remove(child)
                    cell.set("t", "inlineStr")
                    inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
                    text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
                    text.text = value
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                preserved = copy.copy(info)
                after.writestr(preserved, data)
    return output_buffer.getvalue()
