"""Persist proactive mapping review metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_mapping_metadata"
down_revision: str | None = "0008_simple_form_fills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("form_fills", sa.Column("mapping_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("form_fills", "mapping_metadata")
