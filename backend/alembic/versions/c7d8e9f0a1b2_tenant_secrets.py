"""tenant secrets (encrypted workflow secrets store)

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
"""
import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("value_enc", sa.Text(), nullable=False),
        sa.Column("allowed_hosts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_secret_name"),
    )
    op.create_index("ix_tenant_secrets_tenant_id", "tenant_secrets", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_secrets_tenant_id", table_name="tenant_secrets")
    op.drop_table("tenant_secrets")
