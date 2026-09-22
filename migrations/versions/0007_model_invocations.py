"""Model Gateway execution traces."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_model_invocations"
down_revision: str | None = "0006_agencies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(64), nullable=True),
        sa.Column("input_reference", sa.String(256), nullable=True),
        sa.Column("result_reference", sa.String(256), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("stage", "outcome", "result_reference"):
        op.create_index(f"ix_model_invocations_{column}", "model_invocations", [column])


def downgrade() -> None:
    op.drop_table("model_invocations")
