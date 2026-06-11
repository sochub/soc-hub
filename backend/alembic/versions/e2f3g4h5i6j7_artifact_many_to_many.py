"""artifact many to many

Revision ID: e2f3g4h5i6j7
Revises: d1e2f3g4h5i6
Create Date: 2026-02-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e2f3g4h5i6j7"
down_revision = "d1e2f3g4h5i6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create case_artifacts junction table
    op.create_table(
        "case_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("artifact_id", sa.Integer(), sa.ForeignKey("artifacts.id"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("added_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("case_id", "artifact_id", name="uq_case_artifact"),
    )
    op.create_index("ix_case_artifacts_case_id", "case_artifacts", ["case_id"])
    op.create_index("ix_case_artifacts_artifact_id", "case_artifacts", ["artifact_id"])

    # 2. Add isolated column to artifacts
    op.add_column("artifacts", sa.Column("isolated", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # 3. Migrate existing data: copy case_id references into junction table
    op.execute(
        """
        INSERT INTO case_artifacts (case_id, artifact_id, added_at, added_by)
        SELECT case_id, id, created_at, created_by FROM artifacts
        WHERE case_id IS NOT NULL
        """
    )

    # 4. Drop the case_id column from artifacts
    op.drop_constraint("artifacts_case_id_fkey", "artifacts", type_="foreignkey")
    op.drop_column("artifacts", "case_id")


def downgrade() -> None:
    # 1. Re-add case_id column
    op.add_column("artifacts", sa.Column("case_id", sa.Integer(), nullable=True))
    op.create_foreign_key("artifacts_case_id_fkey", "artifacts", "cases", ["case_id"], ["id"])

    # 2. Restore data from junction table (pick the first case_id per artifact)
    op.execute(
        """
        UPDATE artifacts
        SET case_id = ca.case_id
        FROM (
            SELECT DISTINCT ON (artifact_id) artifact_id, case_id
            FROM case_artifacts
            ORDER BY artifact_id, added_at ASC
        ) ca
        WHERE artifacts.id = ca.artifact_id
        """
    )

    # 3. Make case_id NOT NULL again
    op.alter_column("artifacts", "case_id", nullable=False)

    # 4. Drop isolated column
    op.drop_column("artifacts", "isolated")

    # 5. Drop junction table
    op.drop_index("ix_case_artifacts_artifact_id", "case_artifacts")
    op.drop_index("ix_case_artifacts_case_id", "case_artifacts")
    op.drop_table("case_artifacts")
