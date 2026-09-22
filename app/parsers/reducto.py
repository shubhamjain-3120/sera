from typing import Any

import httpx

from app.parsers.base import CanonicalParse


class ReductoParserAdapter:
    """Thin asynchronous provider boundary; native inspection remains authoritative for widgets."""

    def __init__(self, api_key: str, base_url: str, timeout: float = 30):
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def submit(self, filename: str, content: bytes) -> str:
        response = self.client.post(
            "/parse",
            files={"file": (filename, content)},
            data={"options": '{"ocr_mode":"standard","table_output_format":"html"}'},
        )
        response.raise_for_status()
        payload = response.json()
        job_id = payload.get("job_id") or payload.get("id")
        if not job_id:
            raise RuntimeError("Reducto response did not contain a job id")
        return str(job_id)

    def poll(self, job_id: str) -> CanonicalParse | None:
        response = self.client.get(f"/parse/{job_id}")
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status in {"pending", "processing", "queued"}:
            return None
        if status in {"failed", "error"}:
            raise RuntimeError(payload.get("error") or "Reducto parse failed")
        result = payload.get("result", payload)
        blocks = self._normalize_blocks(result)
        return CanonicalParse(blocks=blocks, raw=payload, provider_job_id=job_id, parser_version="reducto-v1")

    @staticmethod
    def _normalize_blocks(result: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for chunk in result.get("chunks", result.get("blocks", [])):
            location = chunk.get("bounding_box") or chunk.get("bbox")
            blocks.append(
                {
                    "type": chunk.get("type", "text"),
                    "text": chunk.get("content") or chunk.get("text", ""),
                    "source": {
                        "page": chunk.get("page") or chunk.get("page_number"),
                        "rect": location,
                        "coordinate_system": chunk.get("coordinate_system", "provider"),
                    },
                }
            )
        return blocks

