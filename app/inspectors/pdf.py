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


def _field_chain(annotation: Any) -> list[Any]:
    """Return the widget and its AcroForm ancestors, nearest first."""
    chain: list[Any] = []
    current = annotation
    visited: set[tuple[int, int]] = set()
    while current is not None:
        reference = getattr(current, "indirect_reference", None)
        identity = (getattr(reference, "idnum", id(current)), getattr(reference, "generation", 0))
        if identity in visited:
            break
        visited.add(identity)
        chain.append(current)
        parent = current.get("/Parent") if hasattr(current, "get") else None
        current = _resolve(parent) if parent is not None else None
    return chain


def _qualified_name(chain: list[Any], fallback: str) -> tuple[str, str]:
    components = [str(item.get("/T")) for item in reversed(chain) if item.get("/T") is not None]
    if not components:
        return fallback, fallback
    return ".".join(components), components[-1]


def _inherited(chain: list[Any], key: str, default: Any = None) -> Any:
    for item in chain:
        if key in item:
            return item[key]
    return default


def _javascript(action: Any) -> str | None:
    if action is None:
        return None
    action = _resolve(action)
    value = action.get("/JS") if hasattr(action, "get") else None
    return str(_resolve(value)) if value is not None else None


def _choice_options(raw_options: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for raw in raw_options or []:
        item = _resolve(raw)
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            export_value, display_value = str(_resolve(item[0])), str(_resolve(item[1]))
        else:
            export_value = display_value = str(item)
        options.append({"export_value": export_value, "display_value": display_value})
    return options


def _format_hints(format_script: str | None, full_name: str) -> dict[str, Any]:
    script = format_script or ""
    hints: dict[str, Any] = {}
    if "AFNumber_Format" in script:
        hints["format_hint"] = "number"
        import re

        match = re.search(r"AFNumber_Format\s*\(\s*(\d+)", script)
        if match:
            hints["decimal_places"] = int(match.group(1))
    elif "AFDate_" in script:
        hints["format_hint"] = "date"
        import re

        match = re.search(r"AFDate_(?:Format|Keystroke)Ex\s*\(\s*['\"]([^'\"]+)", script)
        if match:
            hints["date_format"] = match.group(1)
    lowered = full_name.lower()
    if "format_hint" not in hints:
        if any(token in lowered for token in ("date", "dob", "effective")):
            hints["format_hint"] = "date"
        elif any(token in lowered for token in ("percent", "revenue", "limit", "radius", "count")):
            hints["format_hint"] = "number"
    return hints


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
            chain = _field_chain(annot)
            fallback = f"widget_{widget_count}"
            full_name, name = _qualified_name(chain, fallback)
            field_code = str(_inherited(chain, "/FT", "") or "") or None
            flags = int(_inherited(chain, "/Ff", 0) or 0)
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
                    set(logical_fields[key]["options"] + [state.lstrip("/") for state in states])
                )
                if states:
                    logical_fields[key]["constraints"]["button_states"] = sorted(
                        set(logical_fields[key]["constraints"].get("button_states", []) + states)
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
            options_raw = _inherited(chain, "/Opt", [])
            choice_options = _choice_options(options_raw)
            options = [option["display_value"] for option in choice_options]
            additional_actions = _resolve(_inherited(chain, "/AA", {})) or {}
            format_script = _javascript(additional_actions.get("/F")) if hasattr(additional_actions, "get") else None
            keystroke_script = _javascript(additional_actions.get("/K")) if hasattr(additional_actions, "get") else None
            max_length = _inherited(chain, "/MaxLen")
            constraints: dict[str, Any] = {
                "choice_options": choice_options,
                "format_script": format_script,
                "keystroke_script": keystroke_script,
                **_format_hints(format_script, full_name),
            }
            if kind == "boolean":
                constraints["button_states"] = _button_states(annot)
            if max_length is not None:
                constraints["max_length"] = int(max_length)
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
                "native_full_name": full_name,
                "native_object_id": key,
                "field_type": kind,
                "required": bool(flags & 2) if field_code else None,
                "writable": kind not in {"signature", "action"} and not bool(flags & 1),
                "current_value": str(_inherited(chain, "/V", "") or ""),
                "options": sorted(
                    set(options + ([state.lstrip("/") for state in _button_states(annot)] if kind == "boolean" else []))
                ),
                "choice_options": choice_options,
                "constraints": constraints,
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
    return [str(key) for key in normal.keys() if str(key) != "/Off"]


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
