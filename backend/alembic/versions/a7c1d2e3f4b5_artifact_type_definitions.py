"""artifact_type_definitions + artifacts.custom_type_id

Revision ID: a7c1d2e3f4b5
Revises: f0a1b2c3d4e5
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c1d2e3f4b5"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifact_type_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("show_in_mindmap", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("private", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload_key", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "key", name="uq_artifact_type_tenant_key"),
    )
    op.create_index("ix_artifact_type_definitions_id", "artifact_type_definitions", ["id"])
    op.create_index("ix_artifact_type_definitions_tenant_id", "artifact_type_definitions", ["tenant_id"])
    op.add_column("artifacts", sa.Column("custom_type_id", sa.Integer(),
                  sa.ForeignKey("artifact_type_definitions.id", ondelete="RESTRICT"), nullable=True))
    op.create_index("ix_artifacts_custom_type_id", "artifacts", ["custom_type_id"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_custom_type_id", table_name="artifacts")
    op.drop_column("artifacts", "custom_type_id")
    op.drop_index("ix_artifact_type_definitions_tenant_id", table_name="artifact_type_definitions")
    op.drop_index("ix_artifact_type_definitions_id", table_name="artifact_type_definitions")
    op.drop_table("artifact_type_definitions")
