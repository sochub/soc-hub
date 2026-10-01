"""tenant_ai_configs

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""
import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tenant_ai_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("api_base", sa.String()),
        sa.Column("region", sa.String()),
        sa.Column("project", sa.String()),
        sa.Column("location", sa.String()),
        sa.Column("auth_mode", sa.String()),
        sa.Column("role_arn", sa.String()),
        sa.Column("external_id", sa.String()),
        sa.Column("credentials_enc", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_tenant_ai_configs_id", "tenant_ai_configs", ["id"])


def downgrade():
    op.drop_table("tenant_ai_configs")
