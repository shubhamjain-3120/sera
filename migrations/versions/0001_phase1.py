"""Phase 1 artifacts, processing runs, and template versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_phase1"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False), sa.Column("kind", sa.Enum("PDF", "XLSX", name="artifactkind"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(768), nullable=False, unique=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_sha256", "artifacts", ["sha256"])
    op.create_table(
        "processing_runs",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("status", sa.Enum("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", name="runstatus"), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False), sa.Column("progress", sa.Integer(), nullable=False), sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_job_id", sa.String(256)), sa.Column("parser_version", sa.String(64), nullable=False), sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()), sa.Column("raw_result", sa.JSON()), sa.Column("error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_processing_runs_artifact_id", "processing_runs", ["artifact_id"])
    op.create_table(
        "template_drafts",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), unique=True, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("name", sa.String(256), nullable=False), sa.Column("schema", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "template_versions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("draft_id", sa.String(36), sa.ForeignKey("template_drafts.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("name", sa.String(256), nullable=False), sa.Column("schema", sa.JSON(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False), sa.Column("schema_sha256", sa.String(64), nullable=False), sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("draft_id", "version", name="uq_template_version"),
    )
    op.create_index("ix_template_versions_draft_id", "template_versions", ["draft_id"])


def downgrade() -> None:
    op.drop_table("template_versions")
    op.drop_table("template_drafts")
    op.drop_table("processing_runs")
    op.drop_table("artifacts")
