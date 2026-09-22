import json
import re
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.models import EvidenceSnapshot

TOKEN_RE = re.compile(r"[a-z0-9]+")


def candidate_pools(
    session: Session,
    snapshots: list[EvidenceSnapshot],
    fields: list[dict[str, Any]],
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Retrieve lexical candidates while keeping semantic/type checks in the mapper.

    PostgreSQL uses its built-in full-text index machinery. SQLite and other
    deterministic test profiles return the small frozen bundle for equivalent
    scoring in Python.
    """
    all_facts = [
        (snapshot.id, fact)
        for snapshot in snapshots
        for fact in snapshot.snapshot.get("facts", [])
    ]
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return {field["id"]: all_facts for field in fields}
    statement = text(
        """
        SELECT es.id AS snapshot_id, fact
        FROM evidence_snapshots AS es
        CROSS JOIN LATERAL jsonb_array_elements(es.snapshot::jsonb -> 'facts') AS fact
        WHERE es.id IN :snapshot_ids
          AND to_tsvector(
                'simple',
                coalesce(fact ->> 'key', '') || ' ' ||
                coalesce(fact ->> 'label', '') || ' ' ||
                coalesce(fact ->> 'entity_role', '')
              ) @@ to_tsquery('simple', :query)
        """
    ).bindparams(bindparam("snapshot_ids", expanding=True))
    snapshot_ids = [snapshot.id for snapshot in snapshots]
    pools: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for field in fields:
        raw_query = f"{field.get('semantic_type') or ''} {field.get('label') or ''}"
        tokens = sorted(set(TOKEN_RE.findall(raw_query.casefold())))
        if not tokens:
            pools[field["id"]] = all_facts
            continue
        query = " | ".join(tokens)
        rows = session.execute(
            statement, {"snapshot_ids": snapshot_ids, "query": query}
        ).all()
        pools[field["id"]] = [
            (
                row.snapshot_id,
                json.loads(row.fact) if isinstance(row.fact, str) else row.fact,
            )
            for row in rows
        ]
    return pools
