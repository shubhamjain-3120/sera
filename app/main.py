import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agencies import DETAIL_FACT_KEYS, agency_facts
from app.config import get_settings
from app.db import Base, SessionLocal, engine, get_db
from app.inspectors.pdf import render_pdf_page
from app.inspectors.xlsx import read_sheet_grid
from app.models import (
    Agency,
    Artifact,
    ArtifactKind,
    ArtifactPurpose,
    EvidenceSnapshot,
    FormFill,
    ProcessingRun,
    TemplateDraft,
    TemplateVersion,
)
from app.output_writer import FormWriteError, write_form
from app.schemas import (
    AgencyResponse,
    AgencyUpdate,
    ArtifactResponse,
    CaseResponse,
    DraftResponse,
    DraftUpdate,
    EvidenceSnapshotResponse,
    EvidenceSourceResponse,
    FormFieldGeometryUpdate,
    FormFieldUpdate,
    FormAnnotationsUpdate,
    FormFillCreate,
    FormFillResponse,
    FormFillRunResponse,
    PublishRequest,
    RunResponse,
    VersionResponse,
)
from app.services import (
    RevisionConflict,
    create_form_fill,
    ingest_evidence,
    inspect_artifact,
    new_case_key,
    publish_draft,
    run_form_fill_job,
    seed_agencies,
    update_draft,
    update_form_field,
    update_form_annotations,
)
from app.storage import get_storage

Db = Annotated[Session, Depends(get_db)]
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        seed_agencies(session)
    yield


app = FastAPI(title="AI Form Filler", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):51\d{2}$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def inspect_artifact_background(run_id: str) -> None:
    """BackgroundTasks must not retain the request-scoped database session."""
    with SessionLocal() as session:
        inspect_artifact(session, get_storage(), run_id)


def ingest_evidence_background(run_id: str) -> None:
    with SessionLocal() as session:
        ingest_evidence(session, get_storage(), run_id)


def form_fill_background(run_id: str) -> None:
    with SessionLocal() as session:
        run_form_fill_job(session, run_id)


def _run_response(run: ProcessingRun) -> RunResponse:
    return RunResponse(
        id=run.id,
        artifact_id=run.artifact_id,
        status=run.status.value,
        stage=run.stage,
        progress=run.progress,
        provider=run.provider,
        provider_job_id=run.provider_job_id,
        parser_version=run.parser_version,
        result=run.result,
        error=run.error,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "human-review"}


@app.post("/api/v1/artifacts", response_model=ArtifactResponse, status_code=201)
async def upload_artifact(background: BackgroundTasks, db: Db, file: UploadFile = File(...)) -> ArtifactResponse:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".xlsx"}:
        raise HTTPException(415, "Phase 1 accepts PDF and XLSX targets only")
    storage = get_storage()
    key, sha256, size = storage.put_immutable(file.file, suffix)
    if size > settings.max_upload_bytes:
        raise HTTPException(413, "File exceeds configured upload limit")
    # Storage keys are content-addressed, so identical bytes must reuse the
    # existing target artifact even when the local upload filename differs.
    existing = db.scalar(
        select(Artifact).where(
            Artifact.sha256 == sha256,
            Artifact.purpose == ArtifactPurpose.TARGET,
        )
    )
    artifact = existing or Artifact(
        filename=file.filename or f"upload{suffix}",
        media_type=file.content_type or ("application/pdf" if suffix == ".pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        kind=ArtifactKind.PDF if suffix == ".pdf" else ArtifactKind.XLSX,
        purpose=ArtifactPurpose.TARGET,
        sha256=sha256,
        size_bytes=size,
        storage_key=key,
    )
    if not existing:
        db.add(artifact)
        db.flush()
    draft = db.scalar(select(TemplateDraft).where(TemplateDraft.artifact_id == artifact.id))
    run = ProcessingRun(artifact_id=artifact.id)
    db.add(run)
    if not draft:
        draft = TemplateDraft(artifact_id=artifact.id, name=Path(artifact.filename).stem, schema={"fields": [], "repeating_groups": [], "inspection": {}})
        db.add(draft)
    db.commit()
    db.refresh(run)
    db.refresh(draft)
    background.add_task(inspect_artifact_background, run.id)
    return ArtifactResponse(
        id=artifact.id,
        filename=artifact.filename,
        media_type=artifact.media_type,
        kind=artifact.kind.value,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        created_at=artifact.created_at,
        run_id=run.id,
        draft_id=draft.id,
    )


@app.get("/api/v1/artifacts", response_model=list[ArtifactResponse])
def list_artifacts(db: Db) -> list[ArtifactResponse]:
    artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.purpose == ArtifactPurpose.TARGET)
        .order_by(Artifact.created_at.desc())
    ).all()
    responses = []
    for artifact in artifacts:
        run = db.scalar(select(ProcessingRun).where(ProcessingRun.artifact_id == artifact.id).order_by(ProcessingRun.created_at.desc()))
        draft = db.scalar(select(TemplateDraft).where(TemplateDraft.artifact_id == artifact.id))
        if run and draft:
            responses.append(ArtifactResponse(**{column: getattr(artifact, column) for column in ["id", "filename", "media_type", "sha256", "size_bytes", "created_at"]}, kind=artifact.kind.value, run_id=run.id, draft_id=draft.id))
    return responses


@app.get("/api/v1/artifacts/{artifact_id}/content")
def artifact_content(artifact_id: str, db: Db) -> StreamingResponse:
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    return StreamingResponse(get_storage().open(artifact.storage_key), media_type=artifact.media_type, headers={"Content-Disposition": f'inline; filename="{artifact.filename}"'})


@app.get("/api/v1/artifacts/{artifact_id}/pages/{page}.png")
def pdf_page(artifact_id: str, page: int, db: Db, scale: float = Query(1.5, ge=0.5, le=3)) -> Response:
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.kind != ArtifactKind.PDF:
        raise HTTPException(404, "PDF artifact not found")
    try:
        with get_storage().open(artifact.storage_key) as stream:
            image = render_pdf_page(stream, page, scale)
    except IndexError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(image, media_type="image/png")


@app.get("/api/v1/artifacts/{artifact_id}/pages/{page}/crop.png")
def pdf_page_crop(artifact_id: str, page: int, db: Db,
                  x: float = Query(ge=0, le=1), y: float = Query(ge=0, le=1),
                  width: float = Query(gt=0, le=1), height: float = Query(gt=0, le=1)) -> Response:
    if x + width > 1.000001 or y + height > 1.000001:
        raise HTTPException(422, "Source crop is outside the page")
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.kind != ArtifactKind.PDF:
        raise HTTPException(404, "PDF artifact not found")
    try:
        with get_storage().open(artifact.storage_key) as stream:
            page_image = render_pdf_page(stream, page, 2.0)
    except IndexError as exc:
        raise HTTPException(404, str(exc)) from exc
    with Image.open(BytesIO(page_image)) as image:
        pad_x, pad_y = image.width * 0.015, image.height * 0.015
        bounds = (max(0, int(x * image.width - pad_x)), max(0, int(y * image.height - pad_y)),
                  min(image.width, int((x + width) * image.width + pad_x)),
                  min(image.height, int((y + height) * image.height + pad_y)))
        cropped = image.crop(bounds)
        output = BytesIO()
        cropped.save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png")


@app.get("/api/v1/artifacts/{artifact_id}/sheets/{sheet_name}/grid")
def sheet_grid(artifact_id: str, sheet_name: str, db: Db, min_row: int = 1, max_row: int = Query(50, le=500), min_col: int = 1, max_col: int = Query(20, le=100)) -> dict:
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.kind != ArtifactKind.XLSX:
        raise HTTPException(404, "Workbook artifact not found")
    try:
        with get_storage().open(artifact.storage_key) as stream:
            return read_sheet_grid(stream, sheet_name, min_row, max_row, min_col, max_col)
    except KeyError as exc:
        raise HTTPException(404, "Sheet not found") from exc


SOURCE_KINDS = {
    ".pdf": (ArtifactKind.PDF, "application/pdf"),
    ".xlsx": (ArtifactKind.XLSX, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".png": (ArtifactKind.IMAGE, "image/png"),
    ".jpg": (ArtifactKind.IMAGE, "image/jpeg"),
    ".jpeg": (ArtifactKind.IMAGE, "image/jpeg"),
    ".txt": (ArtifactKind.TEXT, "text/plain"),
}


@app.post("/api/v1/evidence/sources", response_model=EvidenceSourceResponse, status_code=201)
async def upload_evidence_source(
    background: BackgroundTasks,
    db: Db,
    file: UploadFile = File(...),
    case_key: str | None = Query(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"),
) -> EvidenceSourceResponse:
    if settings.evidence_require_reducto and not settings.reducto_api_key:
        raise HTTPException(
            503,
            "Reducto is required for evidence ingestion. Configure REDUCTO_API_KEY and restart the API.",
        )
    # Omitting the case starts a new isolated batch; the response carries the
    # derived key so the rest of one upload batch joins the same case.
    if case_key is None:
        case_key = new_case_key(db)
    normalized_name = (file.filename or "").casefold()
    if any(marker in normalized_name for marker in ("filled", "reference output", "evaluation-only")):
        raise HTTPException(422, "Filled reference outputs are evaluation-only and cannot be evidence sources")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SOURCE_KINDS:
        raise HTTPException(415, "Evidence sources must be PDF, XLSX, PNG, JPEG, or UTF-8 text")
    kind, default_media_type = SOURCE_KINDS[suffix]
    storage = get_storage()
    key, sha256, size = storage.put_immutable(file.file, suffix)
    if size > settings.max_upload_bytes:
        raise HTTPException(413, "File exceeds configured upload limit")
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.sha256 == sha256,
            Artifact.purpose == ArtifactPurpose.SOURCE,
            Artifact.case_key == case_key,
        )
    )
    if artifact is None:
        artifact = Artifact(
            filename=file.filename or f"source{suffix}",
            media_type=file.content_type or default_media_type,
            kind=kind,
            purpose=ArtifactPurpose.SOURCE,
            case_key=case_key,
            sha256=sha256,
            size_bytes=size,
            storage_key=key,
        )
        db.add(artifact)
        db.flush()
    run = ProcessingRun(
        artifact_id=artifact.id,
        stage="evidence-ingestion",
        provider="reducto",
        parser_version="pending",
        config_snapshot={
            "case_key": case_key,
            "source_only": True,
            "allow_native_fallback": False,
        },
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background.add_task(ingest_evidence_background, run.id)
    return EvidenceSourceResponse(
        id=artifact.id,
        filename=artifact.filename,
        media_type=artifact.media_type,
        kind=artifact.kind.value,
        purpose=artifact.purpose.value,
        case_key=artifact.case_key,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        created_at=artifact.created_at,
        run_id=run.id,
    )


@app.get("/api/v1/evidence/sources", response_model=list[EvidenceSourceResponse])
def list_evidence_sources(db: Db, case_key: str | None = None) -> list[EvidenceSourceResponse]:
    query = select(Artifact).where(Artifact.purpose == ArtifactPurpose.SOURCE)
    if case_key is not None:
        query = query.where(Artifact.case_key == case_key)
    artifacts = db.scalars(query.order_by(Artifact.created_at.desc())).all()
    responses = []
    for artifact in artifacts:
        run = db.scalar(
            select(ProcessingRun)
            .where(ProcessingRun.artifact_id == artifact.id)
            .order_by(ProcessingRun.created_at.desc())
        )
        if not run:
            continue
        snapshot = db.scalar(
            select(EvidenceSnapshot)
            .where(EvidenceSnapshot.artifact_id == artifact.id)
            .order_by(EvidenceSnapshot.created_at.desc())
        )
        responses.append(
            EvidenceSourceResponse(
                id=artifact.id,
                filename=artifact.filename,
                media_type=artifact.media_type,
                kind=artifact.kind.value,
                purpose=artifact.purpose.value,
                case_key=artifact.case_key,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                created_at=artifact.created_at,
                run_id=run.id,
                snapshot_id=snapshot.id if snapshot else None,
            )
        )
    return responses


@app.get("/api/v1/evidence/snapshots/{snapshot_id}", response_model=EvidenceSnapshotResponse)
def get_evidence_snapshot(snapshot_id: str, db: Db) -> EvidenceSnapshot:
    snapshot = db.get(EvidenceSnapshot, snapshot_id)
    if not snapshot:
        raise HTTPException(404, "Evidence snapshot not found")
    return snapshot


@app.get("/api/v1/agencies", response_model=list[AgencyResponse])
def list_agencies(db: Db) -> list[Agency]:
    return list(db.scalars(select(Agency).order_by(Agency.is_default.desc(), Agency.name)).all())


@app.put("/api/v1/agencies/{agency_key}", response_model=AgencyResponse)
def put_agency(agency_key: str, update: AgencyUpdate, db: Db) -> Agency:
    agency = db.scalar(select(Agency).where(Agency.key == agency_key))
    if agency is None:
        raise HTTPException(404, "Agency not found")
    unknown = set(update.details) - set(DETAIL_FACT_KEYS)
    if unknown:
        raise HTTPException(422, f"Unsupported agency detail keys: {', '.join(sorted(unknown))}")
    agency.details = {**agency.details, **update.details}
    if update.name:
        agency.name = update.name
    db.commit()
    db.refresh(agency)
    return agency


@app.get("/api/v1/cases", response_model=list[CaseResponse])
def list_cases(db: Db) -> list[CaseResponse]:
    rows = db.execute(
        select(
            Artifact.case_key,
            func.count(Artifact.id),
            func.min(Artifact.created_at),
        )
        .where(Artifact.purpose == ArtifactPurpose.SOURCE, Artifact.case_key.is_not(None))
        .group_by(Artifact.case_key)
    ).all()
    snapshot_counts = dict(
        db.execute(
            select(Artifact.case_key, func.count(EvidenceSnapshot.id))
            .join(EvidenceSnapshot, EvidenceSnapshot.artifact_id == Artifact.id)
            .where(Artifact.purpose == ArtifactPurpose.SOURCE)
            .group_by(Artifact.case_key)
        ).all()
    )
    cases = [
        CaseResponse(
            case_key=case_key,
            source_count=source_count,
            snapshot_count=snapshot_counts.get(case_key, 0),
            created_at=created_at,
        )
        for case_key, source_count, created_at in rows
    ]
    return sorted(cases, key=lambda item: item.created_at, reverse=True)


@app.get("/api/v1/processing-runs/{run_id}", response_model=RunResponse)
def get_run(run_id: str, db: Db) -> RunResponse:
    run = db.get(ProcessingRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return _run_response(run)


@app.get("/api/v1/templates/{draft_id}", response_model=DraftResponse)
def get_draft(draft_id: str, db: Db) -> TemplateDraft:
    draft = db.get(TemplateDraft, draft_id)
    if not draft:
        raise HTTPException(404, "Template draft not found")
    return draft


@app.put("/api/v1/templates/{draft_id}", response_model=DraftResponse)
def put_draft(draft_id: str, update: DraftUpdate, db: Db) -> TemplateDraft:
    draft = db.get(TemplateDraft, draft_id)
    if not draft:
        raise HTTPException(404, "Template draft not found")
    try:
        return update_draft(
            db, draft, update.expected_revision, update.template_schema, update.name
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/v1/templates/{draft_id}/publish", response_model=VersionResponse, status_code=201)
def publish(draft_id: str, request: PublishRequest, db: Db) -> TemplateVersion:
    draft = db.get(TemplateDraft, draft_id)
    if not draft:
        raise HTTPException(404, "Template draft not found")
    try:
        return publish_draft(db, draft, request.expected_revision)
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/v1/templates/{draft_id}/versions", response_model=list[VersionResponse])
def versions(draft_id: str, db: Db) -> list[TemplateVersion]:
    return list(db.scalars(select(TemplateVersion).where(TemplateVersion.draft_id == draft_id).order_by(TemplateVersion.version.desc())).all())


def _snippet_for_fact(snapshot: dict[str, Any], fact: dict[str, Any], snapshot_id: str) -> list[dict[str, Any]]:
    snippets = []
    for provenance in fact.get("provenance", []) or []:
        excerpt = provenance.get("excerpt")
        if excerpt:
            snippets.append({**provenance, "text": excerpt, "snapshot_id": snapshot_id, "artifact_id": provenance.get("artifact_id") or snapshot.get("_artifact_id") or snapshot.get("artifact_id")})
    if snippets:
        return snippets
    for block in snapshot.get("parse_blocks", []) or []:
        block = {"text": block} if isinstance(block, str) else block
        if str(block.get("id") or block.get("block_id")) in {str(x) for x in fact.get("source_block_ids", [])}:
            snippets.append({"text": str(block.get("text") or block.get("content") or ""), "snapshot_id": snapshot_id})
    return snippets


def _form_response(db: Session, fill: FormFill) -> FormFillResponse:
    version = db.get(TemplateVersion, fill.template_version_id)
    answers = fill.answers or []
    def populated(answer: dict[str, Any]) -> bool:
        value = answer.get("write_value")
        return value is not None and (not isinstance(value, str) or bool(value.strip()))
    # A cleared answer no longer consumes its evidence fact. This keeps the
    # review usage indicator aligned with the current editable form state.
    used = {
        fid for answer in answers
        if populated(answer)
        for fid in answer.get("evidence_fact_ids", [])
    }
    snapshots = [db.get(EvidenceSnapshot, sid) for sid in fill.evidence_snapshot_ids or []]
    facts_by_id: dict[str, tuple[dict[str, Any], EvidenceSnapshot]] = {}
    blocks_by_id: dict[str, dict[str, Any]] = {}
    raw_blocks: dict[str, list[dict[str, Any]]] = {}
    for snapshot_row in snapshots:
        if snapshot_row is None:
            continue
        for fact in snapshot_row.snapshot.get("facts", []) or []:
            facts_by_id[str(fact.get("id"))] = (fact, snapshot_row)
        for index, original in enumerate(snapshot_row.snapshot.get("parse_blocks", []) or []):
            block = {"text": original} if isinstance(original, str) else original
            raw_id = str(block.get("id") or block.get("block_id") or index)
            stored_block = {
                "text": str(block.get("text") or block.get("content") or ""),
                "snapshot_id": snapshot_row.id,
                "artifact_id": snapshot_row.artifact_id,
                **(block.get("source") or {}),
            }
            blocks_by_id[f"{snapshot_row.id}:{raw_id}:{index}"] = stored_block
            raw_blocks.setdefault(raw_id, []).append(stored_block)
    agency_row = db.scalar(select(Agency).where(Agency.key == fill.agency_key)) if fill.agency_key else db.scalar(select(Agency).where(Agency.is_default.is_(True)))
    registry_facts = {fact["id"]: fact for fact in agency_facts({"key": agency_row.key, "name": agency_row.name, "details": agency_row.details})} if agency_row else {}
    fields = []
    if version:
        saved_geometry = (fill.mapping_metadata or {}).get("field_geometry") or {}
        for field in version.schema.get("fields", []) or []:
            field = dict(field)
            saved = saved_geometry.get(str(field.get("id")))
            if saved and field.get("location", {}).get("kind") == "pdf_rect":
                x, y, width, height = (float(value) for value in saved.get("rect", []))
                location = dict(field.get("location") or {})
                location.update({"page": int(saved.get("page") or location.get("page") or 1), "rect": [x, y, x + width, y + height], "coordinate_system": "normalized-top-left"})
                field["location"] = location
                widgets = list(field.get("widgets") or [])
                if widgets:
                    widgets[0] = location
                    field["widgets"] = widgets
            answer = next((a for a in answers if a.get("field_id") == field.get("id")), None)
            snippets: list[dict[str, Any]] = []
            original_snippets: list[dict[str, Any]] = []
            if answer:
                for fact_id in answer.get("evidence_fact_ids", []) or []:
                    match = facts_by_id.get(str(fact_id))
                    if match:
                        fact, snapshot_row = match
                        snippets.extend(_snippet_for_fact({**snapshot_row.snapshot, "_artifact_id": snapshot_row.artifact_id}, fact, snapshot_row.id))
                    elif str(fact_id) in registry_facts:
                        fact = registry_facts[str(fact_id)]
                        snippets.append({"text": f"Agency registry — {fact['label']}: {fact['value']}", "source": "agency_registry"})
                for block_id in answer.get("source_block_ids", []) or []:
                    block = blocks_by_id.get(str(block_id))
                    if block is None and len(raw_blocks.get(str(block_id), [])) == 1:
                        block = raw_blocks[str(block_id)][0]
                    if block and block["text"]:
                        snippets.append(block)
                proposal = answer.get("model_proposal") or {}
                for fact_id in proposal.get("evidence_fact_ids", []) or []:
                    match = facts_by_id.get(str(fact_id))
                    if match:
                        fact, snapshot_row = match
                        original_snippets.extend(_snippet_for_fact({**snapshot_row.snapshot, "_artifact_id": snapshot_row.artifact_id}, fact, snapshot_row.id))
                for block_id in proposal.get("source_block_ids", []) or []:
                    block = blocks_by_id.get(str(block_id))
                    if block is None and len(raw_blocks.get(str(block_id), [])) == 1:
                        block = raw_blocks[str(block_id)][0]
                    if block and block["text"]:
                        original_snippets.append(block)
            fields.append({**(answer or {}), "field": field,
                           "write_value": (answer or {}).get("write_value", field.get("current_value")),
                           "origin": (answer or {}).get("origin", "prefilled"),
                           "style": ((fill.mapping_metadata or {}).get("field_styles") or {}).get(str(field.get("id"))),
                           "review_status": ((fill.mapping_metadata or {}).get("review_statuses") or {}).get(str(field.get("id"))),
                           "original_snippets": original_snippets,
                           "snippets": snippets})
    field_order = [str(item.get("id")) for item in (version.schema.get("fields", []) if version else [])]
    order = {field_id: index for index, field_id in enumerate(field_order)}
    dispositions = {item.get("fact_id"): item for item in (fill.mapping_metadata or {}).get("fact_dispositions", [])}
    evidence = []
    for snapshot_row in snapshots:
        if not snapshot_row:
            continue
        for fact in snapshot_row.snapshot.get("facts", []) or []:
            linked = sorted((a.get("field_id") for a in answers if fact.get("id") in a.get("evidence_fact_ids", []) and populated(a)), key=lambda value: order.get(str(value), len(order)))
            disposition = dispositions.get(fact.get("id"), {"disposition": "needs_review", "reason": "No mapping disposition was returned"})
            evidence.append({"fact": fact, "snapshot_id": snapshot_row.id, "artifact_id": snapshot_row.artifact_id, "used": fact.get("id") in used, "field_ids": linked, "disposition": disposition.get("disposition"), "reason": disposition.get("reason")})
    artifact = db.get(Artifact, version.draft.artifact_id) if version and version.draft else None
    for item in (fill.mapping_metadata or {}).get("supplemental_facts", []):
        fact_id = item.get("id") or "supplemental_" + hashlib.sha256(
            f"{item.get('key')}|{item.get('value')}|{item.get('source_block_ids')}".encode()
        ).hexdigest()[:20]
        fact = {"id": fact_id, "key": item.get("key", ""), "label": item.get("label", ""),
                "value": item.get("value"), "raw_value": item.get("raw_value", ""),
                "entity_id": "applicant", "entity_role": "applicant", "confidence": 1.0,
                "uncertainty": [], "source_block_ids": item.get("source_block_ids", []),
                "semantics": ["supplemental_fact"]}
        disposition = dispositions.get(fact_id, {})
        linked = sorted((a.get("field_id") for a in answers if fact_id in a.get("evidence_fact_ids", []) and populated(a)), key=lambda value: order.get(str(value), len(order)))
        evidence.append({"fact": fact, "supplemental_fact": item, "used": bool(linked), "field_ids": linked,
                         "disposition": disposition.get("disposition", "needs_review"),
                         "reason": disposition.get("reason"), "explanation": item.get("explanation")})
    return FormFillResponse(id=fill.id, case_key=fill.case_key, template_version_id=fill.template_version_id, template_name=version.name if version else "", page_count=(version.schema.get("inspection") or {}).get("page_count") if version else None, target_artifact_id=artifact.id if artifact else "", target_kind=artifact.kind.value if artifact else "", status=fill.state, output_available=fill.state == "exported" and bool(fill.output_storage_key), output_error=fill.error, fields=fields, evidence=evidence, answers=answers, mapping_metadata=fill.mapping_metadata or {}, evidence_snapshot_ids=fill.evidence_snapshot_ids or [], agency_key=fill.agency_key, state=fill.state, model_execution_id=fill.model_execution_id, error=fill.error, output_filename=fill.output_filename, output_media_type=fill.output_media_type, output_sha256=fill.output_sha256, created_at=fill.created_at, updated_at=fill.updated_at, approved_at=fill.approved_at)


@app.post("/api/v1/form-fills", response_model=FormFillRunResponse, status_code=202)
def post_form_fill(background: BackgroundTasks, request: FormFillCreate, db: Db) -> FormFillRunResponse:
    try:
        fill, run = create_form_fill(db, case_key=request.case_key, template_version_id=request.template_version_id, snapshot_ids=request.evidence_snapshot_ids, agency_key=request.agency_key)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    background.add_task(form_fill_background, run.id)
    return FormFillRunResponse(**_run_response(run).model_dump(), form_fill_id=fill.id)


@app.get("/api/v1/form-fills", response_model=list[FormFillResponse])
def list_form_fills(db: Db, case_key: str | None = None) -> list[FormFillResponse]:
    query = select(FormFill).order_by(FormFill.updated_at.desc())
    if case_key:
        query = query.where(FormFill.case_key == case_key)
    return [_form_response(db, fill) for fill in db.scalars(query).all()]


@app.get("/api/v1/form-fills/{fill_id}", response_model=FormFillResponse)
def get_form_fill(fill_id: str, db: Db) -> FormFillResponse:
    fill = db.get(FormFill, fill_id)
    if not fill:
        raise HTTPException(404, "Form fill not found")
    return _form_response(db, fill)


@app.patch("/api/v1/form-fills/{fill_id}/fields/{field_id}", response_model=FormFillResponse)
def patch_form_field(fill_id: str, field_id: str, request: FormFieldUpdate, db: Db) -> FormFillResponse:
    fill = db.get(FormFill, fill_id)
    if not fill:
        raise HTTPException(404, "Form fill not found")
    try:
        return _form_response(db, update_form_field(db, fill, field_id, request.write_value, request.evidence_fact_ids, request.geometry,
            write_value_supplied="write_value" in request.model_fields_set, style=request.style, review_status=request.review_status))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.patch("/api/v1/form-fills/{fill_id}/fields/{field_id}/geometry", response_model=FormFillResponse)
def patch_form_field_geometry(fill_id: str, field_id: str, request: FormFieldGeometryUpdate, db: Db) -> FormFillResponse:
    fill = db.get(FormFill, fill_id)
    if not fill:
        raise HTTPException(404, "Form fill not found")
    try:
        return _form_response(db, update_form_field(db, fill, field_id, None, None, request.geometry, write_value_supplied=False))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put("/api/v1/form-fills/{fill_id}/annotations", response_model=FormFillResponse)
def put_form_annotations(fill_id: str, request: FormAnnotationsUpdate, db: Db) -> FormFillResponse:
    fill = db.get(FormFill, fill_id)
    if not fill:
        raise HTTPException(404, "Form fill not found")
    try:
        return _form_response(db, update_form_annotations(db, fill, request.annotations))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v1/form-fills/{fill_id}/approve-and-export", response_model=FormFillResponse)
def approve_and_export(fill_id: str, db: Db) -> FormFillResponse:
    fill = db.get(FormFill, fill_id)
    if fill and fill.state in {"mapping", "mapping_failed"}:
        raise HTTPException(409, "Mapping must finish before approval")
    version = db.get(TemplateVersion, fill.template_version_id) if fill else None
    draft = db.get(TemplateDraft, version.draft_id) if version else None
    artifact = db.get(Artifact, draft.artifact_id) if draft else None
    if not fill or not version or not artifact:
        raise HTTPException(404, "Form fill not found")
    values = {field.get("id"): field.get("current_value") for field in version.schema.get("fields", []) or []}
    values.update({answer.get("field_id"): answer.get("write_value") for answer in fill.answers or []})
    schema_for_output = dict(version.schema)
    schema_for_output["fields"] = [dict(field) for field in version.schema.get("fields", []) or []]
    geometry = (fill.mapping_metadata or {}).get("field_geometry") or {}
    for field in schema_for_output["fields"]:
        item = geometry.get(str(field.get("id")))
        if not item:
            continue
        x, y, width, height = (float(value) for value in item["rect"])
        location = dict(field.get("location") or {})
        location.update({"page": int(item.get("page") or location.get("page") or 1), "rect": [x, y, x + width, y + height], "coordinate_system": "normalized-top-left"})
        field["location"] = location
        widgets = list(field.get("widgets") or [])
        if widgets:
            widgets[0] = location
            field["widgets"] = widgets
    try:
        with get_storage().open(artifact.storage_key) as source:
            output = write_form(source.read(), schema_for_output, values, artifact.kind.value,
                                annotations=(fill.mapping_metadata or {}).get("annotations") or [],
                                field_styles=(fill.mapping_metadata or {}).get("field_styles") or {})
    except FormWriteError as exc:
        fill.state = "export_failed"
        fill.error = f"{exc.field_id}: {exc.message}"
        db.commit()
        raise HTTPException(422, detail={"field_id": exc.field_id, "error": exc.message}) from exc
    suffix = ".pdf" if artifact.kind == ArtifactKind.PDF else ".xlsx"
    key, sha256, _ = get_storage().put_immutable(BytesIO(output), suffix)
    fill.output_storage_key = key
    fill.output_sha256 = sha256
    fill.output_filename = f"{Path(artifact.filename).stem}_filled{suffix}"
    fill.output_media_type = artifact.media_type
    fill.state = "exported"
    fill.error = None
    fill.approved_at = datetime.now(UTC)
    db.commit()
    db.refresh(fill)
    return _form_response(db, fill)


@app.get("/api/v1/form-fills/{fill_id}/output")
def form_fill_output(fill_id: str, db: Db, preview: bool = False) -> StreamingResponse:
    fill = db.get(FormFill, fill_id)
    if not fill or fill.state != "exported" or not fill.output_storage_key:
        raise HTTPException(404, "Current form output is not available")
    disposition = "inline" if preview else "attachment"
    return StreamingResponse(get_storage().open(fill.output_storage_key), media_type=fill.output_media_type, headers={"Content-Disposition": f'{disposition}; filename="{fill.output_filename}"'})
