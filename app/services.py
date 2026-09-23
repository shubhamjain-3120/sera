from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agencies import SEED_AGENCIES, agency_facts
from app.config import get_settings
from app.evidence import extract_evidence, extract_evidence_model, native_parse
from app.inspectors import inspect_pdf, inspect_xlsx
from app.mapping import map_form
from app.model_gateway import ModelGateway
from app.models import (
    Agency,
    Artifact,
    EvidenceSnapshot,
    FormFill,
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
            draft.schema = _reconcile_inspection_annotations(draft.schema, schema)
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


def _reconcile_inspection_annotations(
    previous_schema: dict, inspected_schema: dict
) -> dict:
    """Carry only explicit human annotations onto a fresh native inspection.

    Geometry, widget identities, choices, and PDF constraints always come from
    the new inspection. Semantic and label values inferred by an older inspector
    are intentionally not copied because they may be stale after a parser fix.
    """
    result = dict(inspected_schema)
    new_fields = [dict(field) for field in inspected_schema.get("fields", [])]
    prior_by_identity: dict[str, dict] = {}
    for field in previous_schema.get("fields", []):
        identities = [field.get("native_full_name"), field.get("native_object_id"), field.get("id")]
        for identity in identities:
            if identity:
                prior_by_identity[str(identity)] = field

    for field in new_fields:
        identities = [field.get("native_full_name"), field.get("native_object_id"), field.get("id")]
        prior = next(
            (prior_by_identity[str(identity)] for identity in identities if identity and str(identity) in prior_by_identity),
            None,
        )
        if prior is None:
            continue
        if prior.get("label_origin") == "human":
            field["label"] = prior.get("label")
            field["label_origin"] = "human"
        if prior.get("semantic_type_origin") == "human":
            field["semantic_type"] = prior.get("semantic_type")
            field["semantic_type_origin"] = "human"
        if prior.get("notes"):
            field["notes"] = prior["notes"]
        # Keep a user-configured output date format only when the new field is
        # still date-like. Native inspection constraints otherwise win.
        old_constraints = prior.get("constraints") or {}
        new_constraints = field.get("constraints") or {}
        if old_constraints.get("date_format") and new_constraints.get("format_hint") == "date":
            field["constraints"] = {**new_constraints, "date_format": old_constraints["date_format"]}
    result["fields"] = new_fields
    return result


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
        # This flag is set only by deterministic test/evaluation callers. It
        # deliberately wins over ambient developer credentials so CI fixtures
        # never become dependent on a live provider response.
        if allow_native_fallback:
            parsed = native_parse(run.artifact.kind.value, content)
            provider = "native-test-fallback"
        elif settings.reducto_api_key:
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
        if allow_native_fallback:
            payload = extract_evidence(
                parsed.blocks,
                run.artifact_id,
                run.artifact.sha256,
                provider,
                parsed.parser_version,
            )
        else:
            payload = extract_evidence_model(
                parsed.blocks,
                run.artifact_id,
                run.artifact.sha256,
                provider,
                parsed.parser_version,
                session=session,
                run_id=run.id,
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


def seed_agencies(session: Session) -> None:
    """Ensure the configured agencies exist without overwriting edited details."""
    for seed in SEED_AGENCIES:
        if session.scalar(select(Agency).where(Agency.key == seed["key"])) is None:
            session.add(
                Agency(
                    key=seed["key"],
                    name=seed["name"],
                    details=dict(seed["details"]),
                    is_default=seed["is_default"],
                )
            )
    session.commit()


def resolve_agency(session: Session, agency_key: str | None) -> Agency:
    if agency_key:
        agency = session.scalar(select(Agency).where(Agency.key == agency_key))
        if agency is None:
            raise ValueError(f"Unknown agency {agency_key}")
        return agency
    agency = session.scalar(select(Agency).where(Agency.is_default.is_(True)))
    if agency is None:
        raise ValueError("No default agency is configured")
    return agency


def new_case_key(session: Session) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    existing = set(session.scalars(select(Artifact.case_key).where(Artifact.case_key.like(f"case-{stamp}-%"))).all())
    for sequence in range(1, 1000):
        candidate = f"case-{stamp}-{sequence:03d}"
        if candidate not in existing:
            return candidate
    raise ValueError(f"Exhausted case identifiers for {stamp}")


def resolve_form_snapshots(session: Session, case_key: str, snapshot_ids: list[str] | None) -> list[EvidenceSnapshot]:
    """Resolve an immutable, case-isolated snapshot list for a form fill."""
    if snapshot_ids is not None:
        snapshots = list(session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.id.in_(snapshot_ids))).all())
        if len(snapshots) != len(set(snapshot_ids)):
            raise ValueError("One or more evidence snapshots were not found")
    else:
        rows = session.execute(
            select(EvidenceSnapshot, Artifact)
            .join(Artifact, EvidenceSnapshot.artifact_id == Artifact.id)
            .where(Artifact.case_key == case_key)
            .order_by(EvidenceSnapshot.created_at.desc(), EvidenceSnapshot.id.desc())
        ).all()
        latest: dict[str, EvidenceSnapshot] = {}
        for snapshot, artifact in rows:
            latest.setdefault(artifact.id, snapshot)
        snapshots = list(latest.values())
    if not snapshots:
        raise ValueError(f"Case {case_key} has no evidence snapshots")
    artifacts = {artifact.id: artifact for artifact in session.scalars(
        select(Artifact).where(Artifact.id.in_([s.artifact_id for s in snapshots]))
    ).all()}
    if any(artifacts.get(snapshot.artifact_id) is None or artifacts[snapshot.artifact_id].case_key != case_key for snapshot in snapshots):
        raise ValueError("Evidence snapshots must all belong to the requested case")
    return sorted(snapshots, key=lambda item: item.id)


def create_form_fill(session: Session, *, case_key: str, template_version_id: str,
                     snapshot_ids: list[str] | None, agency_key: str | None) -> tuple[FormFill, ProcessingRun]:
    version = session.get(TemplateVersion, template_version_id)
    if version is None:
        raise ValueError("Published template version not found")
    snapshots = resolve_form_snapshots(session, case_key, snapshot_ids)
    fill = FormFill(case_key=case_key, template_version_id=template_version_id,
                    evidence_snapshot_ids=[s.id for s in snapshots], agency_key=agency_key,
                    answers=[], state="mapping")
    session.add(fill)
    session.flush()
    run = ProcessingRun(
        artifact_id=version.draft.artifact_id,
        provider="openai", stage="queued",
        config_snapshot={"operation": "form_fill", "form_fill_id": fill.id,
                         "case_key": case_key, "template_version_id": template_version_id,
                         "evidence_snapshot_ids": fill.evidence_snapshot_ids,
                         "agency_key": agency_key},
    )
    session.add(run)
    session.commit()
    session.refresh(fill)
    session.refresh(run)
    return fill, run


def run_form_fill_job(session: Session, run_id: str, gateway: ModelGateway | None = None) -> None:
    run = session.get(ProcessingRun, run_id)
    if run is None:
        raise KeyError(run_id)
    config = dict(run.config_snapshot or {})
    fill = session.get(FormFill, config.get("form_fill_id"))
    if fill is None:
        raise ValueError("Form fill no longer exists")
    run.status = RunStatus.RUNNING
    run.stage = "mapping"
    run.progress = 20
    run.started_at = datetime.now(UTC)
    session.commit()
    try:
        version = session.get(TemplateVersion, fill.template_version_id)
        snapshots = [session.get(EvidenceSnapshot, sid) for sid in fill.evidence_snapshot_ids]
        if version is None or any(item is None for item in snapshots):
            raise ValueError("Form fill references are incomplete")
        agency = resolve_agency(session, fill.agency_key)
        result = map_form(version.schema,
                          [(item.id, {**item.snapshot, "_artifact_id": item.artifact_id}) for item in snapshots if item],
                          agency_facts({"key": agency.key, "name": agency.name, "details": agency.details}),
                          session=session, run_id=run.id, gateway=gateway)
        mappable = {
            str(field.get("id")) for field in version.schema.get("fields", [])
            if field.get("writable") is not False
            and field.get("field_type") not in {"action", "signature"}
            and (field.get("current_value") is None or not str(field["current_value"]).strip())
        }
        dispositions = result.get("fact_dispositions", [])
        supplements = result.get("supplemental_facts", [])
        supplemental_ids = {
            "supplemental_" + hashlib.sha256(
                f"{item.get('key')}|{item.get('value')}|{item.get('source_block_ids')}".encode()
            ).hexdigest()[:20]: item
            for item in supplements
        }
        # Store mapping metadata on the fill so review can account for every
        # source fact without requiring a second model call.
        fill.answers = []
        for answer in result.get("answers", []):
            if answer.get("field_id") not in mappable:
                continue
            answer = dict(answer, origin="model")
            cited_blocks = set(answer.get("source_block_ids", []))
            answer["evidence_fact_ids"] = list(answer.get("evidence_fact_ids", [])) + [
                identifier for identifier, item in supplemental_ids.items()
                if cited_blocks.intersection(item.get("source_block_ids", [])) and identifier not in answer.get("evidence_fact_ids", [])
            ]
            fill.answers.append(answer)
        fill.mapping_metadata = {"fact_dispositions": dispositions, "supplemental_facts": supplements}
        fill.model_execution_id = (result.get("model_profile") or {}).get("execution_id")
        fill.state = "mapped"
        fill.error = None
        run.result = {"form_fill_id": fill.id, "fact_dispositions": dispositions, "supplemental_facts": supplements}
        run.status = RunStatus.SUCCEEDED
        run.stage = "complete"
        run.progress = 100
        run.finished_at = datetime.now(UTC)
        session.commit()
    except Exception as exc:
        session.rollback()
        failed = session.get(ProcessingRun, run_id)
        current = session.get(FormFill, config.get("form_fill_id"))
        if failed:
            failed.status = RunStatus.FAILED
            failed.stage = "failed"
            failed.error = f"{type(exc).__name__}: {exc}"
            failed.finished_at = datetime.now(UTC)
        if current:
            current.state = "mapping_failed"
            current.error = f"{type(exc).__name__}: {exc}"
        session.commit()


def update_form_field(session: Session, fill: FormFill, field_id: str, write_value: object,
                      evidence_fact_ids: list[str] | None = None,
                      geometry: dict[str, Any] | None = None) -> FormFill:
    if fill.state in {"mapping", "mapping_failed"}:
        raise ValueError("Mapping must finish before review edits")
    version = session.get(TemplateVersion, fill.template_version_id)
    field = next((item for item in version.schema.get("fields", []) if item.get("id") == field_id), None) if version else None
    if field is None:
        raise ValueError("Template field was not found")
    if field.get("writable") is False or field.get("field_type") in {"action", "signature"}:
        raise ValueError("Template field is not writable")
    geometry_only = geometry is not None and write_value is None and evidence_fact_ids is None
    if not geometry_only:
        answers = [dict(answer) for answer in (fill.answers or []) if answer.get("field_id") != field_id]
        answer = {"field_id": field_id, "write_value": write_value, "origin": "human"}
        if evidence_fact_ids is not None:
            answer["evidence_fact_ids"] = evidence_fact_ids
        answers.append(answer)
        fill.answers = answers
    if geometry is not None:
        rect = geometry.get("rect") if isinstance(geometry, dict) else None
        page = geometry.get("page") if isinstance(geometry, dict) else None
        if not isinstance(rect, list) or len(rect) != 4 or not all(isinstance(item, (int, float)) for item in rect):
            raise ValueError("Field geometry must contain four numeric normalized coordinates")
        x, y, width, height = (float(item) for item in rect)
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1 and x + width <= 1 and y + height <= 1):
            raise ValueError("Field geometry must fit within the PDF page")
        metadata = dict(fill.mapping_metadata or {})
        geometries = dict(metadata.get("field_geometry") or {})
        geometries[field_id] = {"page": int(page or field.get("location", {}).get("page") or 1), "rect": [x, y, width, height], "coordinate_system": "normalized-top-left"}
        metadata["field_geometry"] = geometries
        fill.mapping_metadata = metadata
    fill.state = "mapped"
    fill.error = None
    fill.output_storage_key = fill.output_filename = fill.output_media_type = fill.output_sha256 = None
    fill.approved_at = None
    session.commit()
    session.refresh(fill)
    return fill
