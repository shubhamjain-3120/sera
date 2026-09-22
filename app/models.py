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


class EvidenceBundle(Base):
    """A frozen, case-scoped set of immutable evidence snapshots."""

    __tablename__ = "evidence_bundles"
    __table_args__ = (
        UniqueConstraint("case_key", "bundle_sha256", name="uq_evidence_bundle_hash"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_key: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_ids: Mapped[list[str]] = mapped_column(JSON)
    bundle_sha256: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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


class FillPlan(Base):
    __tablename__ = "fill_plans"
    __table_args__ = (
        # Agency details feed values into the plan, so the same template and
        # evidence filed for a different agency is a different Fill Plan.
        UniqueConstraint(
            "template_version_id",
            "evidence_bundle_id",
            "agency_key",
            "mapping_profile_hash",
            name="uq_fill_plan_inputs",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_key: Mapped[str] = mapped_column(String(64), index=True)
    agency_key: Mapped[str] = mapped_column(String(64), index=True)
    mapping_profile_hash: Mapped[str] = mapped_column(String(64), default="legacy-profile-v1", index=True)
    template_version_id: Mapped[str] = mapped_column(
        ForeignKey("template_versions.id"), index=True
    )
    evidence_bundle_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_bundles.id"), index=True
    )
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    template_version: Mapped[TemplateVersion] = relationship()
    evidence_bundle: Mapped[EvidenceBundle] = relationship()


class FillPlanRevision(Base):
    """Immutable mapping result. A changed proposal creates a new revision."""

    __tablename__ = "fill_plan_revisions"
    __table_args__ = (
        UniqueConstraint("fill_plan_id", "revision", name="uq_fill_plan_revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    fill_plan_id: Mapped[str] = mapped_column(ForeignKey("fill_plans.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    mapper_version: Mapped[str] = mapped_column(String(64))
    payload_sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    fill_plan: Mapped[FillPlan] = relationship()


class VerificationReport(Base):
    """Immutable verification result for one exact Fill Plan revision."""

    __tablename__ = "verification_reports"
    __table_args__ = (
        UniqueConstraint(
            "fill_plan_revision_id", "verifier_version", name="uq_verification_revision_version"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    fill_plan_revision_id: Mapped[str] = mapped_column(
        ForeignKey("fill_plan_revisions.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    deterministic_version: Mapped[str] = mapped_column(String(64))
    verifier_version: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_sha256: Mapped[str] = mapped_column(String(64), index=True)
    report_sha256: Mapped[str] = mapped_column(String(64), index=True)
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    fill_plan_revision: Mapped[FillPlanRevision] = relationship()


class ReviewDecision(Base):
    """Append-only human decision pinned to source and resulting revisions."""

    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint("resulting_revision_id", name="uq_review_resulting_revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    fill_plan_id: Mapped[str] = mapped_column(ForeignKey("fill_plans.id"), index=True)
    source_revision_id: Mapped[str] = mapped_column(
        ForeignKey("fill_plan_revisions.id"), index=True
    )
    resulting_revision_id: Mapped[str] = mapped_column(
        ForeignKey("fill_plan_revisions.id"), unique=True, index=True
    )
    target_field_id: Mapped[str] = mapped_column(String(256), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    previous_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
