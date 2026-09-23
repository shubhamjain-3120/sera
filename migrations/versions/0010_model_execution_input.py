"""Store request payloads for model execution traces."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_model_execution_input"
down_revision: str | None = "0009_mapping_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_executions", sa.Column("input_payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_executions", "input_payload")
