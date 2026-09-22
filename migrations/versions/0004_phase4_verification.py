"""Phase 4 immutable verification reports."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_phase4"
down_revision: str | None = "0003_phase3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "verification_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fill_plan_revision_id", sa.String(36), sa.ForeignKey("fill_plan_revisions.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("deterministic_version", sa.String(64), nullable=False),
        sa.Column("verifier_version", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fill_plan_revision_id", "verifier_version", name="uq_verification_revision_version"),
    )
    op.create_index("ix_verification_reports_fill_plan_revision_id", "verification_reports", ["fill_plan_revision_id"])
    op.create_index("ix_verification_reports_status", "verification_reports", ["status"])
    op.create_index("ix_verification_reports_input_sha256", "verification_reports", ["input_sha256"])
    op.create_index("ix_verification_reports_report_sha256", "verification_reports", ["report_sha256"])


def downgrade() -> None:
    op.drop_table("verification_reports")
