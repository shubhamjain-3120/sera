from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CanonicalParse:
    blocks: list[dict[str, Any]]
    raw: dict[str, Any]
    provider_job_id: str | None
    parser_version: str


class ParserAdapter(Protocol):
    def submit(self, filename: str, content: bytes) -> str: ...
    def poll(self, job_id: str) -> CanonicalParse | None: ...

