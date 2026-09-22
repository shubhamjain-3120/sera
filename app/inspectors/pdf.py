import hashlib
from io import BytesIO
from typing import Any, BinaryIO

from pypdf import PdfReader

from app.inspectors.layout import (
    canonical_blocks_to_layout,
    enrich_fields,
    extract_pdf_layout,
)


def _field_type(code: str | None, flags: int) -> str:
    if code == "/Sig":
        return "signature"
    if code == "/Btn":
        if flags & (1 << 16):
            return "action"
        return "boolean"
    if code == "/Ch":
        return "choice"
    if code == "/Tx":
        return "text"
    return "unknown"


def _resolve(obj: Any) -> Any:
    return obj.get_object() if hasattr(obj, "get_object") else obj


def inspect_pdf(
    stream: BinaryIO, provider_blocks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    content = stream.read()
    reader = PdfReader(BytesIO(content))
    logical_fields: dict[str, dict[str, Any]] = {}
    widget_count = 0
    seen: set[str] = set()
    for page_index, page in enumerate(reader.pages):
        rotation = int(page.get("/Rotate", 0) or 0)
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        for annot_ref in page.get("/Annots", []):
            annot = _resolve(annot_ref)
            if annot.get("/Subtype") != "/Widget":
                continue
            widget_count += 1
            parent_ref = annot.get("/Parent")
            parent = _resolve(parent_ref or {})
            name = str(annot.get("/T") or parent.get("/T") or f"widget_{widget_count}")
            field_code = str(annot.get("/FT") or parent.get("/FT") or "") or None
            flags = int(annot.get("/Ff") or parent.get("/Ff") or 0)
            kind = _field_type(field_code, flags)
            rect = [float(value) for value in annot.get("/Rect", [0, 0, 0, 0])]
            # A widget with its own /T or /FT is itself a terminal field. Its
            # parent can be a non-terminal hierarchy node shared by unrelated
            # siblings (as in the operations-category percentage grid). Only
            # inherited widgets, such as radio/Yes-No appearances, should be
            # reconciled through their terminal parent.
            widget_is_terminal = annot.get("/T") is not None or annot.get("/FT") is not None
            if widget_is_terminal and hasattr(annot_ref, "idnum"):
                key = f"widget:{annot_ref.idnum}"
            elif parent_ref is not None and hasattr(parent_ref, "idnum"):
                key = f"parent:{parent_ref.idnum}"
            elif hasattr(annot_ref, "idnum"):
                key = f"widget:{annot_ref.idnum}"
            else:
                key = f"{name}:{page_index}:{','.join(map(str, rect))}"
            if key in seen:
                logical_fields[key]["widgets"].append(
                    {
                        "kind": "pdf_rect",
                        "page": page_index + 1,
                        "rect": rect,
                        "rotation": rotation,
                        "page_width": page_width,
                        "page_height": page_height,
                    }
                )
                logical_fields[key]["widget_count"] += 1
                states = _button_states(annot) if kind == "boolean" else []
                logical_fields[key]["options"] = sorted(
                    set(logical_fields[key]["options"] + states)
                )
                logical_fields[key]["widget_options"].append(
                    {
                        "export_value": states[0] if states else None,
                        "label": None,
                        "location": logical_fields[key]["widgets"][-1],
                    }
                )
                continue
            seen.add(key)
            options_raw = annot.get("/Opt") or parent.get("/Opt") or []
            options = []
            for option in options_raw:
                option = _resolve(option)
                options.append(str(option[1] if isinstance(option, list) and len(option) > 1 else option))
            location = {
                "kind": "pdf_rect",
                "page": page_index + 1,
                "rect": rect,
                "rotation": rotation,
                "page_width": page_width,
                "page_height": page_height,
            }
            logical_fields[key] = {
                "id": hashlib.sha1(key.encode()).hexdigest()[:16],
                "label": name,
                "native_name": name,
                "field_type": kind,
                "required": bool(flags & 2) if field_code else None,
                "writable": kind not in {"signature", "action"} and not bool(flags & 1),
                "current_value": str(annot.get("/V") or parent.get("/V") or ""),
                "options": sorted(
                    set(options + (_button_states(annot) if kind == "boolean" else []))
                ),
                "location": location,
                "widgets": [location],
                "widget_count": 1,
                "widget_options": [
                    {
                        "export_value": (
                            _button_states(annot)[0]
                            if kind == "boolean" and _button_states(annot)
                            else None
                        ),
                        "label": None,
                        "location": location,
                    }
                ],
            }
    fields = list(logical_fields.values())
    tree_fields = reader.get_fields() or {}
    warnings = [] if fields else [
        "No native widgets found; flat-field detection requires parser or manual fields."
    ]
    enrichment = {"high": 0, "medium": 0, "low": 0, "unresolved": len(fields)}
    layout_block_count = 0
    try:
        native_layout = extract_pdf_layout(content)
        provider_layout = canonical_blocks_to_layout(provider_blocks or [])
        layout_block_count = len(native_layout) + len(provider_layout)
        enrichment = enrich_fields(fields, native_layout, provider_layout)
    except Exception as exc:
        warnings.append(f"Layout enrichment unavailable: {type(exc).__name__}: {exc}")
    return {
        "fields": fields,
        "repeating_groups": [],
        "inspection": {
            "format": "pdf",
            "page_count": len(reader.pages),
            "field_tree_count": len(tree_fields),
            "widget_count": widget_count,
            "logical_field_count": len(fields),
            "encrypted": reader.is_encrypted,
            "detection": "native-widgets",
            "layout_block_count": layout_block_count,
            "semantic_enrichment": enrichment,
            "warnings": warnings,
        },
    }


def _button_states(annotation: Any) -> list[str]:
    appearances = _resolve(annotation.get("/AP", {}))
    normal = _resolve(appearances.get("/N", {})) if appearances else {}
    if not hasattr(normal, "keys"):
        return []
    return [str(key).lstrip("/") for key in normal.keys() if str(key) != "/Off"]


def render_pdf_page(stream: BinaryIO, page: int, scale: float = 1.5) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(stream.read())
    if page < 1 or page > len(document):
        raise IndexError("PDF page out of range")
    bitmap = document[page - 1].render(scale=scale, draw_annots=True)
    image = bitmap.to_pil()
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
