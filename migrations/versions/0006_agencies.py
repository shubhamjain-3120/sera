"""Agency registry and per-agency Fill Plan scoping."""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from app.agencies import SEED_AGENCIES

revision: str = "0006_agencies"
down_revision: str | None = "0005_phase5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    agencies = op.create_table(
        "agencies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agencies_key", "agencies", ["key"], unique=True)
    now = datetime.now(UTC)
    op.bulk_insert(
        agencies,
        [
            {
                "id": str(uuid.uuid4()),
                "key": seed["key"],
                "name": seed["name"],
                "details": seed["details"],
                "is_default": seed["is_default"],
                "created_at": now,
                "updated_at": now,
            }
            for seed in SEED_AGENCIES
        ],
    )
    default_key = next(seed["key"] for seed in SEED_AGENCIES if seed["is_default"])
    with op.batch_alter_table("fill_plans") as batch:
        batch.add_column(
            sa.Column("agency_key", sa.String(64), nullable=False, server_default=default_key)
        )
    op.create_index("ix_fill_plans_agency_key", "fill_plans", ["agency_key"])
    with op.batch_alter_table("fill_plans") as batch:
        batch.drop_constraint("uq_fill_plan_inputs", type_="unique")
        batch.create_unique_constraint(
            "uq_fill_plan_inputs", ["template_version_id", "evidence_bundle_id", "agency_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("fill_plans") as batch:
        batch.drop_constraint("uq_fill_plan_inputs", type_="unique")
        batch.create_unique_constraint(
            "uq_fill_plan_inputs", ["template_version_id", "evidence_bundle_id"]
        )
    op.drop_index("ix_fill_plans_agency_key", table_name="fill_plans")
    with op.batch_alter_table("fill_plans") as batch:
        batch.drop_column("agency_key")
    op.drop_index("ix_agencies_key", table_name="agencies")
    op.drop_table("agencies")
