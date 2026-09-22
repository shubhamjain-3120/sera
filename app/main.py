from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agencies import DETAIL_FACT_KEYS
from app.config import get_settings
from app.db import Base, SessionLocal, engine, get_db
from app.inspectors.pdf import render_pdf_page
from app.inspectors.xlsx import read_sheet_grid
from app.models import (
    Agency,
    Artifact,
    ArtifactKind,
    ArtifactPurpose,
    EvidenceBundle,
    EvidenceSnapshot,
    FillPlan,
    FillPlanRevision,
    ProcessingRun,
    ReviewDecision,
    TemplateDraft,
    TemplateVersion,
    VerificationReport,
)
from app.schemas import (
    AgencyResponse,
    AgencyUpdate,
    ArtifactResponse,
    CaseResponse,
    DraftResponse,
    DraftUpdate,
    EvidenceSnapshotResponse,
    EvidenceSourceResponse,
    FillPlanCreate,
    FillPlanResponse,
    FillPlanSummary,
    FillPlanUpdate,
    PublishRequest,
    ReviewDecisionCreate,
    ReviewDecisionResponse,
    RunResponse,
    VerificationReportResponse,
    VersionResponse,
)
from app.services import (
    RevisionConflict,
    create_fill_plan,
    ingest_evidence,
    inspect_artifact,
    new_case_key,
    publish_draft,
    review_fill_plan,
    revise_fill_plan,
    seed_agencies,
    update_draft,
    verify_fill_plan_revision,
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


def _fill_plan_response(db: Session, fill_plan: FillPlan) -> FillPlanResponse:
    version = db.get(TemplateVersion, fill_plan.template_version_id)
    bundle = db.get(EvidenceBundle, fill_plan.evidence_bundle_id)
    revision = db.scalar(
        select(FillPlanRevision).where(
            FillPlanRevision.fill_plan_id == fill_plan.id,
            FillPlanRevision.revision == fill_plan.current_revision,
        )
    )
    if version is None or bundle is None or revision is None:
        raise HTTPException(500, "Fill Plan references are incomplete")
    draft = db.get(TemplateDraft, version.draft_id)
    artifact = db.get(Artifact, draft.artifact_id) if draft else None
    if draft is None or artifact is None:
        raise HTTPException(500, "Fill Plan target is incomplete")
    summary = revision.payload.get("summary", {})
    return FillPlanResponse(
        id=fill_plan.id,
        case_key=fill_plan.case_key,
        template_version_id=version.id,
        template_name=version.name,
        target_artifact_id=artifact.id,
        target_kind=artifact.kind.value,
        evidence_bundle_id=bundle.id,
        evidence_bundle_sha256=bundle.bundle_sha256,
        current_revision=fill_plan.current_revision,
        issue_count=int(summary.get("issue_count", 0)),
        blocker_count=int(summary.get("blocker_count", 0)),
        created_at=fill_plan.created_at,
        updated_at=fill_plan.updated_at,
        revision_id=revision.id,
        mapper_version=revision.mapper_version,
        payload_sha256=revision.payload_sha256,
        payload=revision.payload,
    )


@app.post("/api/v1/fill-plans", response_model=FillPlanResponse, status_code=201)
def post_fill_plan(request: FillPlanCreate, db: Db) -> FillPlanResponse:
    version = db.get(TemplateVersion, request.template_version_id)
    if version is None:
        raise HTTPException(404, "Published template version not found")
    try:
        fill_plan = create_fill_plan(
            db, request.case_key, version, request.evidence_snapshot_ids, request.agency_key
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _fill_plan_response(db, fill_plan)


@app.get("/api/v1/fill-plans", response_model=list[FillPlanSummary])
def list_fill_plans(db: Db, case_key: str | None = None) -> list[FillPlanSummary]:
    query = select(FillPlan)
    if case_key is not None:
        query = query.where(FillPlan.case_key == case_key)
    plans = db.scalars(query.order_by(FillPlan.updated_at.desc())).all()
    return [FillPlanSummary(**_fill_plan_response(db, plan).model_dump()) for plan in plans]


@app.get("/api/v1/fill-plans/{fill_plan_id}", response_model=FillPlanResponse)
def get_fill_plan(fill_plan_id: str, db: Db) -> FillPlanResponse:
    fill_plan = db.get(FillPlan, fill_plan_id)
    if fill_plan is None:
        raise HTTPException(404, "Fill Plan not found")
    return _fill_plan_response(db, fill_plan)


@app.put("/api/v1/fill-plans/{fill_plan_id}", response_model=FillPlanResponse)
def put_fill_plan(fill_plan_id: str, update: FillPlanUpdate, db: Db) -> FillPlanResponse:
    fill_plan = db.get(FillPlan, fill_plan_id)
    if fill_plan is None:
        raise HTTPException(404, "Fill Plan not found")
    try:
        revise_fill_plan(
            db,
            fill_plan,
            update.expected_revision,
            update.selected_candidates,
            [item.model_dump(mode="json") for item in update.derivations],
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _fill_plan_response(db, fill_plan)


def _verification_response(db: Session, report: VerificationReport) -> VerificationReportResponse:
    revision = db.get(FillPlanRevision, report.fill_plan_revision_id)
    if revision is None:
        raise HTTPException(500, "Verification report revision is missing")
    return VerificationReportResponse(
        id=report.id,
        fill_plan_revision_id=report.fill_plan_revision_id,
        fill_plan_revision=revision.revision,
        status=report.status,
        deterministic_version=report.deterministic_version,
        verifier_version=report.verifier_version,
        provider=report.provider,
        model=report.model,
        input_sha256=report.input_sha256,
        report_sha256=report.report_sha256,
        report=report.report,
        created_at=report.created_at,
    )


@app.post(
    "/api/v1/fill-plans/{fill_plan_id}/verifications",
    response_model=VerificationReportResponse,
    status_code=201,
)
def post_verification(
    fill_plan_id: str, db: Db, revision: int | None = Query(default=None, ge=1)
) -> VerificationReportResponse:
    fill_plan = db.get(FillPlan, fill_plan_id)
    if fill_plan is None:
        raise HTTPException(404, "Fill Plan not found")
    try:
        report = verify_fill_plan_revision(db, fill_plan, revision)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _verification_response(db, report)


@app.get(
    "/api/v1/fill-plans/{fill_plan_id}/verifications",
    response_model=list[VerificationReportResponse],
)
def list_verifications(fill_plan_id: str, db: Db) -> list[VerificationReportResponse]:
    fill_plan = db.get(FillPlan, fill_plan_id)
    if fill_plan is None:
        raise HTTPException(404, "Fill Plan not found")
    reports = db.scalars(
        select(VerificationReport)
        .join(FillPlanRevision)
        .where(FillPlanRevision.fill_plan_id == fill_plan_id)
        .order_by(FillPlanRevision.revision.desc(), VerificationReport.created_at.desc())
    ).all()
    return [_verification_response(db, report) for report in reports]


def _review_response(db: Session, decision: ReviewDecision) -> ReviewDecisionResponse:
    source = db.get(FillPlanRevision, decision.source_revision_id)
    resulting = db.get(FillPlanRevision, decision.resulting_revision_id)
    if source is None or resulting is None:
        raise HTTPException(500, "Review decision revision is missing")
    return ReviewDecisionResponse(
        id=decision.id,
        fill_plan_id=decision.fill_plan_id,
        source_revision_id=decision.source_revision_id,
        source_revision=source.revision,
        resulting_revision_id=decision.resulting_revision_id,
        resulting_revision=resulting.revision,
        target_field_id=decision.target_field_id,
        action=decision.action,
        actor=decision.actor,
        reason=decision.reason,
        candidate_id=decision.candidate_id,
        previous_value=decision.previous_value,
        new_value=decision.new_value,
        detail=decision.detail,
        created_at=decision.created_at,
    )


@app.post(
    "/api/v1/fill-plans/{fill_plan_id}/review-decisions",
    response_model=ReviewDecisionResponse,
    status_code=201,
)
def post_review_decision(
    fill_plan_id: str, request: ReviewDecisionCreate, db: Db
) -> ReviewDecisionResponse:
    fill_plan = db.get(FillPlan, fill_plan_id)
    if fill_plan is None:
        raise HTTPException(404, "Fill Plan not found")
    try:
        decision = review_fill_plan(
            db,
            fill_plan,
            expected_revision=request.expected_revision,
            target_field_id=request.target_field_id,
            action=request.action,
            actor=request.actor,
            reason=request.reason,
            candidate_id=request.candidate_id,
            value=request.value,
        )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _review_response(db, decision)


@app.get(
    "/api/v1/fill-plans/{fill_plan_id}/review-decisions",
    response_model=list[ReviewDecisionResponse],
)
def list_review_decisions(fill_plan_id: str, db: Db) -> list[ReviewDecisionResponse]:
    if db.get(FillPlan, fill_plan_id) is None:
        raise HTTPException(404, "Fill Plan not found")
    decisions = db.scalars(
        select(ReviewDecision)
        .where(ReviewDecision.fill_plan_id == fill_plan_id)
        .order_by(ReviewDecision.created_at.desc())
    ).all()
    return [_review_response(db, decision) for decision in decisions]
