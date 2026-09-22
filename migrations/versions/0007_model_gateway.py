"""Add immutable model execution traces and mapping profile identity."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_model_gateway"
down_revision: str | None = "0006_agencies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("model_executions"):
        op.create_table(
            "model_executions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("idempotency_key", sa.String(128), nullable=False),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("processing_runs.id"), nullable=True),
            sa.Column("stage", sa.String(64), nullable=False),
            sa.Column("input_sha256", sa.String(64), nullable=False),
            sa.Column("output_sha256", sa.String(64), nullable=True),
            sa.Column("output_payload", sa.JSON(), nullable=True),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("model", sa.String(128), nullable=False),
            sa.Column("reasoning_effort", sa.String(32), nullable=False),
            sa.Column("prompt_version", sa.String(64), nullable=False),
            sa.Column("schema_version", sa.String(64), nullable=False),
            sa.Column("response_id", sa.String(128), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("usage", sa.JSON(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("refusal", sa.Text(), nullable=True),
            sa.Column("incomplete_details", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("idempotency_key", name="uq_model_executions_idempotency_key"),
        )
    index_names = {item["name"] for item in sa.inspect(bind).get_indexes("model_executions")}
    for name, column in (
        ("ix_model_executions_idempotency_key", "idempotency_key"),
        ("ix_model_executions_run_id", "run_id"),
        ("ix_model_executions_stage", "stage"),
        ("ix_model_executions_input_sha256", "input_sha256"),
    ):
        if name not in index_names:
            op.create_index(name, "model_executions", [column], unique=name.endswith("idempotency_key"))

    fill_columns = {item["name"] for item in sa.inspect(bind).get_columns("fill_plans")}
    with op.batch_alter_table("fill_plans") as batch:
        if "mapping_profile_hash" not in fill_columns:
            batch.add_column(
                sa.Column("mapping_profile_hash", sa.String(64), nullable=False, server_default="legacy-profile-v1")
            )
        constraints = {item.get("name") for item in sa.inspect(bind).get_unique_constraints("fill_plans")}
        if "uq_fill_plan_inputs" in constraints:
            batch.drop_constraint("uq_fill_plan_inputs", type_="unique")
        batch.create_unique_constraint(
            "uq_fill_plan_inputs",
            ["template_version_id", "evidence_bundle_id", "agency_key", "mapping_profile_hash"],
        )
    if "ix_fill_plans_mapping_profile_hash" not in {item["name"] for item in sa.inspect(bind).get_indexes("fill_plans") }:
        op.create_index("ix_fill_plans_mapping_profile_hash", "fill_plans", ["mapping_profile_hash"])


def downgrade() -> None:
    op.drop_index("ix_fill_plans_mapping_profile_hash", table_name="fill_plans")
    with op.batch_alter_table("fill_plans") as batch:
        batch.drop_constraint("uq_fill_plan_inputs", type_="unique")
        batch.drop_column("mapping_profile_hash")
        batch.create_unique_constraint(
            "uq_fill_plan_inputs", ["template_version_id", "evidence_bundle_id", "agency_key"]
        )
    op.drop_index("ix_model_executions_input_sha256", table_name="model_executions")
    op.drop_index("ix_model_executions_stage", table_name="model_executions")
    op.drop_index("ix_model_executions_run_id", table_name="model_executions")
    op.drop_index("ix_model_executions_idempotency_key", table_name="model_executions")
    op.drop_table("model_executions")
