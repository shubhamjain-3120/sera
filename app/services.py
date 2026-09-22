import hashlib
import json
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.evidence import extract_evidence, native_parse
from app.evidence.retrieve import candidate_pools
from app.inspectors import inspect_pdf, inspect_xlsx
from app.mapping import MAPPER_VERSION, apply_revision, build_fill_plan
from app.models import (
    Artifact,
    EvidenceBundle,
    EvidenceSnapshot,
    FillPlan,
    FillPlanRevision,
    ProcessingRun,
    RunStatus,
    TemplateDraft,
    TemplateVersion,
)
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


def ingest_evidence(session: Session, storage: ObjectStorage, run_id: str) -> None:
    """Parse first, then perform semantic extraction into one immutable snapshot."""
    run = session.get(ProcessingRun, run_id)
    if not run:
        raise KeyError(run_id)
    run.status = RunStatus.RUNNING
    run.stage = "parsing"
    run.progress = 8
    run.started_at = datetime.now(UTC)
    session.commit()
    try:
        with storage.open(run.artifact.storage_key) as source:
            content = source.read()
        settings = get_settings()
        allow_native_fallback = bool(run.config_snapshot.get("allow_native_fallback", False))
        if settings.reducto_api_key:
            adapter = ReductoParserAdapter(settings.reducto_api_key, settings.reducto_base_url)
            job_id = adapter.submit(run.artifact.filename, content)
            run.provider_job_id = job_id
            run.provider = "reducto"
            session.commit()
            # Reducto queues larger/scanned documents asynchronously. Allow up to
            # two minutes before marking the ingestion run as timed out.
            for _ in range(240):
                candidate = adapter.poll(job_id)
                if candidate is not None:
                    parsed = candidate
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError("Reducto parse did not finish within the ingestion window")
            provider = "reducto"
        elif allow_native_fallback:
            parsed = native_parse(run.artifact.kind.value, content)
            provider = "native-test-fallback"
        else:
            raise RuntimeError(
                "Reducto is required for evidence ingestion. Configure REDUCTO_API_KEY; "
                "native OCR is disabled for production evidence."
            )
        run.stage = "semantic-extraction"
        run.progress = 65
        run.provider = provider
        run.parser_version = parsed.parser_version
        run.raw_result = parsed.raw
        session.commit()
        payload = extract_evidence(
            parsed.blocks,
            run.artifact_id,
            run.artifact.sha256,
            provider,
            parsed.parser_version,
        )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        snapshot_hash = hashlib.sha256(canonical).hexdigest()
        snapshot = session.scalar(
            select(EvidenceSnapshot).where(
                EvidenceSnapshot.artifact_id == run.artifact_id,
                EvidenceSnapshot.snapshot_sha256 == snapshot_hash,
            )
        )
        if snapshot is None:
            snapshot = EvidenceSnapshot(
                artifact_id=run.artifact_id,
                run_id=run.id,
                parser_provider=provider,
                parser_version=parsed.parser_version,
                extractor_version=payload["extractor_version"],
                snapshot_sha256=snapshot_hash,
                snapshot=payload,
            )
            session.add(snapshot)
            session.flush()
        run.result = {"snapshot_id": snapshot.id, **payload}
        run.status = RunStatus.SUCCEEDED
        run.stage = "complete"
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


def create_fill_plan(
    session: Session,
    case_key: str,
    template_version: TemplateVersion,
    snapshot_ids: list[str] | None = None,
) -> FillPlan:
    if snapshot_ids is None:
        rows = session.execute(
            select(EvidenceSnapshot, Artifact)
            .join(Artifact, EvidenceSnapshot.artifact_id == Artifact.id)
            .where(Artifact.case_key == case_key)
            .order_by(EvidenceSnapshot.created_at.desc(), EvidenceSnapshot.id.desc())
        ).all()
        # A case bundle contains one current immutable snapshot per source artifact.
        # Older parser/extractor snapshots remain addressable but are never mixed
        # into a new Fill Plan implicitly.
        latest_by_artifact: dict[str, EvidenceSnapshot] = {}
        for snapshot, artifact in rows:
            latest_by_artifact.setdefault(artifact.id, snapshot)
        snapshots = list(latest_by_artifact.values())
    else:
        snapshots = list(
            session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.id.in_(snapshot_ids))).all()
        )
        if len(snapshots) != len(set(snapshot_ids)):
            raise ValueError("One or more evidence snapshots were not found")
        artifacts = list(
            session.scalars(
                select(Artifact).where(Artifact.id.in_([snapshot.artifact_id for snapshot in snapshots]))
            ).all()
        )
        if any(artifact.case_key != case_key for artifact in artifacts):
            raise ValueError("Evidence snapshots must all belong to the requested case")
    if not snapshots:
        raise ValueError(f"Case {case_key} has no evidence snapshots")
    ordered = sorted(snapshots, key=lambda item: item.id)
    bundle_material = [
        {"id": snapshot.id, "sha256": snapshot.snapshot_sha256} for snapshot in ordered
    ]
    bundle_hash = hashlib.sha256(
        json.dumps(bundle_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    bundle = session.scalar(
        select(EvidenceBundle).where(
            EvidenceBundle.case_key == case_key, EvidenceBundle.bundle_sha256 == bundle_hash
        )
    )
    if bundle is None:
        bundle = EvidenceBundle(
            case_key=case_key,
            snapshot_ids=[snapshot.id for snapshot in ordered],
            bundle_sha256=bundle_hash,
        )
        session.add(bundle)
        session.flush()
    existing = session.scalar(
        select(FillPlan).where(
            FillPlan.template_version_id == template_version.id,
            FillPlan.evidence_bundle_id == bundle.id,
        )
    )
    if existing:
        return existing
    payload = build_fill_plan(
        template_version.schema,
        [(snapshot.id, snapshot.snapshot) for snapshot in ordered],
        candidate_pools(session, ordered, template_version.schema.get("fields", [])),
    )
    payload["evidence_bundle"] = {
        "id": bundle.id,
        "case_key": case_key,
        "snapshot_ids": bundle.snapshot_ids,
        "sha256": bundle.bundle_sha256,
    }
    payload["template_version"] = {
        "id": template_version.id,
        "version": template_version.version,
        "schema_sha256": template_version.schema_sha256,
    }
    fill_plan = FillPlan(
        case_key=case_key,
        template_version_id=template_version.id,
        evidence_bundle_id=bundle.id,
    )
    session.add(fill_plan)
    session.flush()
    revision = _fill_plan_revision(fill_plan.id, 1, payload)
    session.add(revision)
    session.commit()
    session.refresh(fill_plan)
    return fill_plan


def revise_fill_plan(
    session: Session,
    fill_plan: FillPlan,
    expected_revision: int,
    selected_candidates: dict[str, str | None],
    derivations: list[dict[str, object]],
) -> FillPlanRevision:
    if fill_plan.current_revision != expected_revision:
        raise RevisionConflict(
            f"Expected revision {expected_revision}, current revision is {fill_plan.current_revision}"
        )
    current = session.scalar(
        select(FillPlanRevision).where(
            FillPlanRevision.fill_plan_id == fill_plan.id,
            FillPlanRevision.revision == fill_plan.current_revision,
        )
    )
    if current is None:
        raise ValueError("Current Fill Plan revision is missing")
    payload = apply_revision(current.payload, selected_candidates, derivations)
    next_number = fill_plan.current_revision + 1
    revision = _fill_plan_revision(fill_plan.id, next_number, payload)
    session.add(revision)
    fill_plan.current_revision = next_number
    session.commit()
    session.refresh(revision)
    return revision


def _fill_plan_revision(fill_plan_id: str, revision: int, payload: dict) -> FillPlanRevision:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return FillPlanRevision(
        fill_plan_id=fill_plan_id,
        revision=revision,
        mapper_version=MAPPER_VERSION,
        payload_sha256=hashlib.sha256(canonical).hexdigest(),
        payload=payload,
    )
