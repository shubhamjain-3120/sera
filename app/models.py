import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import Session as OrmSession

from app.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class ArtifactKind(StrEnum):
    PDF = "pdf"
    XLSX = "xlsx"
    IMAGE = "image"
    TEXT = "text"


class ArtifactPurpose(StrEnum):
    TARGET = "target"
    SOURCE = "source"
    EVALUATION = "evaluation"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(128))
    kind: Mapped[ArtifactKind] = mapped_column(Enum(ArtifactKind))
    purpose: Mapped[ArtifactPurpose] = mapped_column(
        Enum(ArtifactPurpose), default=ArtifactPurpose.TARGET, index=True
    )
    case_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    # Multiple artifact roles may refer to the same immutable content-addressed object.
    storage_key: Mapped[str] = mapped_column(String(768))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProcessingRun(Base):
    __tablename__ = "processing_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), index=True)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.QUEUED)
    stage: Mapped[str] = mapped_column(String(64), default="inspection")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(64), default="native")
    provider_job_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), default="native-v1")
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    artifact: Mapped[Artifact] = relationship()


class ModelExecution(Base):
    """Append-only trace for one model gateway request, including failures."""

    __tablename__ = "model_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("processing_runs.id"), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(64), index=True)
    input_sha256: Mapped[str] = mapped_column(String(64), index=True)
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="openai")
    model: Mapped[str] = mapped_column(String(128))
    reasoning_effort: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(64))
    response_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    refusal: Mapped[str | None] = mapped_column(Text, nullable=True)
    incomplete_details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


@event.listens_for(OrmSession, "before_flush")
def _model_executions_are_append_only(session: OrmSession, _flush_context: Any, _instances: Any) -> None:
    for execution in session.dirty:
        if isinstance(execution, ModelExecution) and session.is_modified(execution, include_collections=True):
            raise ValueError("ModelExecution records are immutable")
    for execution in session.deleted:
        if isinstance(execution, ModelExecution):
            raise ValueError("ModelExecution records are immutable")


class TemplateDraft(Base):
    __tablename__ = "template_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), unique=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(256))
    schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    artifact: Mapped[Artifact] = relationship()


class TemplateVersion(Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("draft_id", "version", name="uq_template_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    draft_id: Mapped[str] = mapped_column(ForeignKey("template_drafts.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(256))
    schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_revision: Mapped[int] = mapped_column(Integer)
    schema_sha256: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    draft: Mapped[TemplateDraft] = relationship()


class EvidenceSnapshot(Base):
    """An immutable semantic view over one unchanged source artifact."""

    __tablename__ = "evidence_snapshots"
    __table_args__ = (
        UniqueConstraint("artifact_id", "snapshot_sha256", name="uq_evidence_snapshot_hash"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("processing_runs.id"), unique=True)
    parser_provider: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64), index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    artifact: Mapped[Artifact] = relationship()
    run: Mapped[ProcessingRun] = relationship()


class Agency(Base):
    """A filing agency whose own details are a trusted, non-document fact source."""

    __tablename__ = "agencies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class FormFill(Base):
    __tablename__ = "form_fills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_key: Mapped[str] = mapped_column(String(64), index=True)
    template_version_id: Mapped[str] = mapped_column(ForeignKey("template_versions.id"), index=True)
    evidence_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    agency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    answers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    mapping_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="mapped", index=True)
    output_storage_key: Mapped[str | None] = mapped_column(String(768), nullable=True)
    output_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    output_media_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    template_version: Mapped[TemplateVersion] = relationship()
