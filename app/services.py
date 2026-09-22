import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.inspectors import inspect_pdf, inspect_xlsx
from app.models import ProcessingRun, RunStatus, TemplateDraft, TemplateVersion
from app.parsers.reducto import ReductoParserAdapter
from app.schemas import TemplateSchema
from app.storage import ObjectStorage


class RevisionConflict(Exception):
    pass


def inspect_artifact(session: Session, storage: ObjectStorage, run_id: str) -> None:
    run = session.get(ProcessingRun, run_id)
    if not run:
        raise KeyError(run_id)
    run.status = RunStatus.RUNNING
    run.started_at = datetime.now(UTC)
    run.progress = 10
    session.commit()
    try:
        with storage.open(run.artifact.storage_key) as source:
            content = source.read()
        if run.artifact.kind.value == "pdf":
            provider_blocks = None
            provider_error = None
            settings = get_settings()
            if settings.reducto_api_key:
                try:
                    parsed = ReductoParserAdapter(
                        settings.reducto_api_key, settings.reducto_base_url
                    ).parse(run.artifact.filename, content)
                    provider_blocks = parsed.blocks
                    run.provider = "reducto+native"
                    run.provider_job_id = parsed.provider_job_id
                    run.parser_version = parsed.parser_version
                    run.raw_result = parsed.raw
                    run.config_snapshot = {
                        "provider": "reducto",
                        "layout_reconciliation": "geometry-v1",
                    }
                except Exception as exc:
                    provider_error = f"{type(exc).__name__}: {exc}"
            schema = inspect_pdf(BytesIO(content), provider_blocks)
            if provider_error:
                schema["inspection"]["warnings"].append(
                    f"Reducto enrichment failed; native layout was used: {provider_error}"
                )
        else:
            schema = inspect_xlsx(BytesIO(content))
        run.progress = 90
        run.result = schema
        draft = session.scalar(select(TemplateDraft).where(TemplateDraft.artifact_id == run.artifact_id))
        if draft:
            draft.schema = schema
            draft.revision += 1
        else:
            draft = TemplateDraft(
                artifact_id=run.artifact_id,
                name=Path(run.artifact.filename).stem,
                schema=schema,
            )
            session.add(draft)
        run.status = RunStatus.SUCCEEDED
        run.progress = 100
        run.finished_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        run = session.get(ProcessingRun, run_id)
        if run:
            run.status = RunStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = datetime.now(UTC)
            session.commit()
        raise


def update_draft(session: Session, draft: TemplateDraft, expected_revision: int, schema: TemplateSchema, name: str | None) -> TemplateDraft:
    if draft.revision != expected_revision:
        raise RevisionConflict(f"Expected revision {expected_revision}, current revision is {draft.revision}")
    draft.schema = schema.model_dump(mode="json")
    if name is not None:
        draft.name = name
    draft.revision += 1
    session.commit()
    session.refresh(draft)
    return draft


def publish_draft(session: Session, draft: TemplateDraft, expected_revision: int) -> TemplateVersion:
    if draft.revision != expected_revision:
        raise RevisionConflict(f"Expected revision {expected_revision}, current revision is {draft.revision}")
    version_number = session.scalar(select(func.max(TemplateVersion.version)).where(TemplateVersion.draft_id == draft.id)) or 0
    canonical = json.dumps(draft.schema, sort_keys=True, separators=(",", ":")).encode()
    version = TemplateVersion(
        draft_id=draft.id,
        version=version_number + 1,
        name=draft.name,
        schema=draft.schema,
        source_revision=draft.revision,
        schema_sha256=hashlib.sha256(canonical).hexdigest(),
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return version
