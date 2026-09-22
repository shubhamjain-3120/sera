"""Phase 2 source artifacts and immutable evidence snapshots."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase2"
down_revision: str | None = "0001_phase1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE artifactkind ADD VALUE IF NOT EXISTS 'IMAGE'")
        op.execute("ALTER TYPE artifactkind ADD VALUE IF NOT EXISTS 'TEXT'")
    purpose = sa.Enum("TARGET", "SOURCE", "EVALUATION", name="artifactpurpose")
    purpose.create(bind, checkfirst=True)
    if bind.dialect.name == "postgresql":
        op.drop_constraint("artifacts_storage_key_key", "artifacts", type_="unique")
    else:
        with op.batch_alter_table(
            "artifacts",
            recreate="always",
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch:
            batch.drop_constraint("uq_artifacts_storage_key", type_="unique")
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("purpose", purpose, nullable=False, server_default="TARGET"))
        batch.add_column(sa.Column("case_key", sa.String(64), nullable=True))
        batch.create_index("ix_artifacts_purpose", ["purpose"])
        batch.create_index("ix_artifacts_case_key", ["case_key"])
    op.create_table(
        "evidence_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("processing_runs.id"), nullable=False, unique=True),
        sa.Column("parser_provider", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(64), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("artifact_id", "snapshot_sha256", name="uq_evidence_snapshot_hash"),
    )
    op.create_index("ix_evidence_snapshots_artifact_id", "evidence_snapshots", ["artifact_id"])
    op.create_index("ix_evidence_snapshots_snapshot_sha256", "evidence_snapshots", ["snapshot_sha256"])


def downgrade() -> None:
    op.drop_table("evidence_snapshots")
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_index("ix_artifacts_case_key")
        batch.drop_index("ix_artifacts_purpose")
        batch.drop_column("case_key")
        batch.drop_column("purpose")
    sa.Enum(name="artifactpurpose").drop(op.get_bind(), checkfirst=True)
