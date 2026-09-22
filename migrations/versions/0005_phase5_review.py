"""Phase 5 append-only human review decisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_phase5"
down_revision: str | None = "0004_phase4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fill_plan_id", sa.String(36), sa.ForeignKey("fill_plans.id"), nullable=False),
        sa.Column("source_revision_id", sa.String(36), sa.ForeignKey("fill_plan_revisions.id"), nullable=False),
        sa.Column("resulting_revision_id", sa.String(36), sa.ForeignKey("fill_plan_revisions.id"), nullable=False),
        sa.Column("target_field_id", sa.String(256), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(256), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("candidate_id", sa.String(256), nullable=True),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resulting_revision_id", name="uq_review_resulting_revision"),
    )
    for column in ("fill_plan_id", "source_revision_id", "resulting_revision_id", "target_field_id", "action"):
        op.create_index(f"ix_review_decisions_{column}", "review_decisions", [column])


def downgrade() -> None:
    op.drop_table("review_decisions")
