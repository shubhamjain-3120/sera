import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.parsers.base import CanonicalParse


class ReductoParserAdapter:
    """Reducto-only asynchronous document parsing boundary."""

    def __init__(self, api_key: str, base_url: str, timeout: float = 30):
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        # Presigned object-store uploads must not receive the Reducto bearer token.
        self.upload_client = httpx.Client(timeout=timeout)
        self._submitted: dict[str, dict[str, Any]] = {}

    def submit(self, filename: str, content: bytes) -> str:
        upload_response = self.client.post("/upload", json={})
        upload_response.raise_for_status()
        upload = upload_response.json()
        file_id = upload.get("file_id")
        presigned_url = upload.get("presigned_url")
        if not file_id or not presigned_url:
            raise RuntimeError("Reducto upload response was missing file_id or presigned_url")
        stored = self.upload_client.put(
            str(presigned_url),
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        stored.raise_for_status()
        response = self.client.post(
            "/parse",
            json={
                "input": file_id,
                "async": {"priority": False},
                "settings": {"force_url_result": True, "return_ocr_data": True},
            },
        )
        response.raise_for_status()
        payload = response.json()
        job_id = payload.get("job_id") or payload.get("id")
        if not job_id:
            raise RuntimeError("Reducto response did not contain a job id")
        self._submitted[str(job_id)] = payload
        return str(job_id)

    def parse(self, filename: str, content: bytes) -> CanonicalParse:
        """Convenience wrapper for target enrichment using the async provider API."""
        job_id = self.submit(filename, content)
        for _ in range(240):
            parsed = self.poll(job_id)
            if parsed is not None:
                return parsed
            time.sleep(0.5)
        raise TimeoutError("Reducto parse did not finish within the target-enrichment window")

    def poll(self, job_id: str) -> CanonicalParse | None:
        payload = self._submitted.get(job_id)
        if payload is None or payload.get("result") is None:
            response = self.client.get(f"/job/{job_id}")
            response.raise_for_status()
            payload = response.json()
        status = str(payload.get("status", "completed")).lower()
        if status in {"pending", "processing", "queued"}:
            return None
        if status in {"failed", "error", "idle"}:
            error = payload.get("error") or payload.get("reason") or "Reducto parse failed"
            raise RuntimeError(str(error))
        parse_response = payload.get("result", payload)
        if not isinstance(parse_response, dict):
            raise RuntimeError("Reducto parse response did not contain an object result")
        result = self._resolve_result(parse_response.get("result", parse_response))
        blocks = self._normalize_blocks(result)
        return CanonicalParse(blocks=blocks, raw=payload, provider_job_id=job_id, parser_version="reducto-v1")

    def _resolve_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("type") == "url":
            url = str(result.get("url", ""))
            if urlparse(url).scheme != "https":
                raise RuntimeError("Reducto result URL must use HTTPS")
            # Result URLs are presigned object-store URLs. Never forward the API key.
            response = self.upload_client.get(url)
            response.raise_for_status()
            downloaded = response.json()
            if not isinstance(downloaded, dict):
                raise RuntimeError("Reducto result URL did not return an object")
            return self._resolve_result(downloaded.get("result", downloaded))
        nested = result.get("result")
        return self._resolve_result(nested) if isinstance(nested, dict) else result

    @staticmethod
    def _normalize_blocks(result: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        ocr_words = (result.get("ocr") or {}).get("words", [])
        for chunk in result.get("chunks", result.get("blocks", [])):
            # V3 chunks contain the provider's precise semantic blocks. Falling
            # back to the chunk itself keeps compatibility with recorded legacy
            # responses without collapsing current results into page-sized text.
            provider_blocks = chunk.get("blocks") or [chunk]
            for provider_block in provider_blocks:
                bbox = provider_block.get("bbox") or provider_block.get("bounding_box")
                page = provider_block.get("page") or provider_block.get("page_number")
                rect = bbox
                if isinstance(bbox, dict):
                    left = bbox.get("left")
                    top = bbox.get("top")
                    width = bbox.get("width")
                    height = bbox.get("height")
                    page = bbox.get("original_page") or bbox.get("page") or page
                    if all(isinstance(value, (int, float)) for value in (left, top, width, height)):
                        rect = [left, top, left + width, top + height]
                    else:
                        rect = None
                matching_words = []
                if isinstance(rect, list) and len(rect) == 4:
                    for word in ocr_words:
                        word_bbox = word.get("bbox") or {}
                        word_page = word_bbox.get("original_page") or word_bbox.get("page")
                        if word_page != page:
                            continue
                        word_rect = ReductoParserAdapter._rect_from_bbox(word_bbox)
                        if word_rect and ReductoParserAdapter._rect_inside(word_rect, rect):
                            matching_words.append(
                                {
                                    "text": word.get("text", ""),
                                    "rect": word_rect,
                                    "confidence": word.get("confidence"),
                                }
                            )
                confidence = provider_block.get("confidence")
                ocr_confidence = {"high": 0.98, "low": 0.65}.get(str(confidence).lower())
                blocks.append(
                    {
                        "type": str(provider_block.get("type", "text")).casefold().replace(" ", "_"),
                        "text": provider_block.get("content") or provider_block.get("text", ""),
                        "ocr_confidence": ocr_confidence,
                        "ocr_words": matching_words,
                        "source": {
                            "kind": "pdf_rect",
                            "page": page,
                            "rect": rect,
                            "coordinate_system": "reducto-normalized-top-left",
                            "rotation": provider_block.get("rotation", 0),
                            "rotation_transform": provider_block.get(
                                "rotation_transform", [1, 0, 0, 1, 0, 0]
                            ),
                            "page_width": provider_block.get("page_width"),
                            "page_height": provider_block.get("page_height"),
                        },
                    }
                )
        return blocks
    @staticmethod
    def _rect_from_bbox(bbox: dict[str, Any]) -> list[float] | None:
        values = [bbox.get(name) for name in ("left", "top", "width", "height")]
        if not all(isinstance(value, (int, float)) for value in values):
            return None
        left, top, width, height = values
        return [float(left), float(top), float(left + width), float(top + height)]

    @staticmethod
    def _rect_inside(inner: list[float], outer: list[float]) -> bool:
        center_x = (inner[0] + inner[2]) / 2
        center_y = (inner[1] + inner[3]) / 2
        return outer[0] <= center_x <= outer[2] and outer[1] <= center_y <= outer[3]
