"""Phase 3 frozen evidence bundles and fill plan revisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_phase3"
down_revision: str | None = "0002_phase2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_bundles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_key", sa.String(64), nullable=False),
        sa.Column("snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("case_key", "bundle_sha256", name="uq_evidence_bundle_hash"),
    )
    op.create_index("ix_evidence_bundles_case_key", "evidence_bundles", ["case_key"])
    op.create_index("ix_evidence_bundles_bundle_sha256", "evidence_bundles", ["bundle_sha256"])
    op.create_table(
        "fill_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_key", sa.String(64), nullable=False),
        sa.Column("template_version_id", sa.String(36), sa.ForeignKey("template_versions.id"), nullable=False),
        sa.Column("evidence_bundle_id", sa.String(36), sa.ForeignKey("evidence_bundles.id"), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_version_id", "evidence_bundle_id", name="uq_fill_plan_inputs"),
    )
    op.create_index("ix_fill_plans_case_key", "fill_plans", ["case_key"])
    op.create_index("ix_fill_plans_template_version_id", "fill_plans", ["template_version_id"])
    op.create_index("ix_fill_plans_evidence_bundle_id", "fill_plans", ["evidence_bundle_id"])
    op.create_table(
        "fill_plan_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fill_plan_id", sa.String(36), sa.ForeignKey("fill_plans.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("mapper_version", sa.String(64), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fill_plan_id", "revision", name="uq_fill_plan_revision"),
    )
    op.create_index("ix_fill_plan_revisions_fill_plan_id", "fill_plan_revisions", ["fill_plan_id"])
    op.create_index("ix_fill_plan_revisions_payload_sha256", "fill_plan_revisions", ["payload_sha256"])


def downgrade() -> None:
    op.drop_table("fill_plan_revisions")
    op.drop_table("fill_plans")
    op.drop_table("evidence_bundles")
