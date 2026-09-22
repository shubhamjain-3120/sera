import hashlib
from typing import Any, BinaryIO

from pypdf import PdfReader


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


def inspect_pdf(stream: BinaryIO) -> dict[str, Any]:
    reader = PdfReader(stream)
    fields: list[dict[str, Any]] = []
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
            parent = _resolve(annot.get("/Parent", {}))
            name = str(annot.get("/T") or parent.get("/T") or f"widget_{widget_count}")
            field_code = str(annot.get("/FT") or parent.get("/FT") or "") or None
            flags = int(annot.get("/Ff") or parent.get("/Ff") or 0)
            rect = [float(value) for value in annot.get("/Rect", [0, 0, 0, 0])]
            key = f"{name}:{page_index}:{','.join(map(str, rect))}"
            if key in seen:
                continue
            seen.add(key)
            kind = _field_type(field_code, flags)
            options_raw = annot.get("/Opt") or parent.get("/Opt") or []
            options = []
            for option in options_raw:
                option = _resolve(option)
                options.append(str(option[1] if isinstance(option, list) and len(option) > 1 else option))
            fields.append(
                {
                    "id": hashlib.sha1(key.encode()).hexdigest()[:16],
                    "label": name,
                    "native_name": name,
                    "field_type": kind,
                    "required": bool(flags & 2) if field_code else None,
                    "writable": kind not in {"signature", "action"} and not bool(flags & 1),
                    "current_value": str(annot.get("/V") or parent.get("/V") or ""),
                    "options": options,
                    "location": {
                        "kind": "pdf_rect",
                        "page": page_index + 1,
                        "rect": rect,
                        "rotation": rotation,
                        "page_width": page_width,
                        "page_height": page_height,
                    },
                }
            )
    tree_fields = reader.get_fields() or {}
    return {
        "fields": fields,
        "repeating_groups": [],
        "inspection": {
            "format": "pdf",
            "page_count": len(reader.pages),
            "field_tree_count": len(tree_fields),
            "widget_count": widget_count,
            "encrypted": reader.is_encrypted,
            "detection": "native-widgets",
            "warnings": [] if fields else ["No native widgets found; flat-field detection requires parser or manual fields."],
        },
    }


def render_pdf_page(stream: BinaryIO, page: int, scale: float = 1.5) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(stream.read())
    if page < 1 or page > len(document):
        raise IndexError("PDF page out of range")
    bitmap = document[page - 1].render(scale=scale, draw_annots=True)
    image = bitmap.to_pil()
    from io import BytesIO

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
