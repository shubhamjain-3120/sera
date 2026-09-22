from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Base, SessionLocal, engine, get_db
from app.inspectors.pdf import render_pdf_page
from app.inspectors.xlsx import read_sheet_grid
from app.models import Artifact, ArtifactKind, ProcessingRun, TemplateDraft, TemplateVersion
from app.schemas import (
    ArtifactResponse,
    DraftResponse,
    DraftUpdate,
    PublishRequest,
    RunResponse,
    VersionResponse,
)
from app.services import RevisionConflict, inspect_artifact, publish_draft, update_draft
from app.storage import get_storage

Db = Annotated[Session, Depends(get_db)]
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="AI Form Filler", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def inspect_artifact_background(run_id: str) -> None:
    """BackgroundTasks must not retain the request-scoped database session."""
    with SessionLocal() as session:
        inspect_artifact(session, get_storage(), run_id)


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
    return {"status": "ok", "phase": "template-inspector"}


@app.post("/api/v1/artifacts", response_model=ArtifactResponse, status_code=201)
async def upload_artifact(background: BackgroundTasks, db: Db, file: UploadFile = File(...)) -> ArtifactResponse:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".xlsx"}:
        raise HTTPException(415, "Phase 1 accepts PDF and XLSX targets only")
    storage = get_storage()
    key, sha256, size = storage.put_immutable(file.file, suffix)
    if size > settings.max_upload_bytes:
        raise HTTPException(413, "File exceeds configured upload limit")
    existing = db.scalar(select(Artifact).where(Artifact.sha256 == sha256, Artifact.filename == file.filename))
    artifact = existing or Artifact(
        filename=file.filename or f"upload{suffix}",
        media_type=file.content_type or ("application/pdf" if suffix == ".pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        kind=ArtifactKind.PDF if suffix == ".pdf" else ArtifactKind.XLSX,
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
    artifacts = db.scalars(select(Artifact).order_by(Artifact.created_at.desc())).all()
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
