"""Replace legacy fill plans and verification records with simple form fills."""

import sqlalchemy as sa
from alembic import op

revision = "0008_simple_form_fills"
down_revision = "0007_model_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("review_decisions", "verification_reports", "fill_plan_revisions", "fill_plans", "evidence_bundles"):
        op.drop_table(table)
    op.create_table(
        "form_fills",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("case_key", sa.String(length=64), nullable=False),
        sa.Column("template_version_id", sa.String(length=36), sa.ForeignKey("template_versions.id"), nullable=False),
        sa.Column("evidence_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("agency_key", sa.String(length=64), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("model_execution_id", sa.String(length=36), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="mapped"),
        sa.Column("output_storage_key", sa.String(length=768), nullable=True),
        sa.Column("output_filename", sa.String(length=512), nullable=True),
        sa.Column("output_media_type", sa.String(length=128), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_form_fills_case_key", "form_fills", ["case_key"])
    op.create_index("ix_form_fills_template_version_id", "form_fills", ["template_version_id"])
    op.create_index("ix_form_fills_agency_key", "form_fills", ["agency_key"])
    op.create_index("ix_form_fills_state", "form_fills", ["state"])


def downgrade() -> None:
    op.drop_index("ix_form_fills_state", table_name="form_fills")
    op.drop_index("ix_form_fills_agency_key", table_name="form_fills")
    op.drop_index("ix_form_fills_template_version_id", table_name="form_fills")
    op.drop_index("ix_form_fills_case_key", table_name="form_fills")
    op.drop_table("form_fills")
