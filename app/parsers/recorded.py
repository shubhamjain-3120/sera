from copy import deepcopy
from typing import Any

from app.parsers.base import CanonicalParse


class RecordedParserAdapter:
    """Deterministic adapter for CI and replaying redacted provider responses."""

    def __init__(self, response: dict[str, Any], version: str = "recorded-v1"):
        self.response = deepcopy(response)
        self.version = version

    def submit(self, filename: str, content: bytes) -> str:
        return f"recorded:{filename}:{len(content)}"

    def poll(self, job_id: str) -> CanonicalParse:
        return CanonicalParse(
            blocks=deepcopy(self.response.get("blocks", [])),
            raw=deepcopy(self.response),
            provider_job_id=job_id,
            parser_version=self.version,
        )
