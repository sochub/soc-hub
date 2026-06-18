"""webhooks table (multi per-tenant) + migrate single key

Revision ID: c2d3e4f5a6b7
Revises: b3c4d5e6f7a8
Create Date: 2026-06-18 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("api_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key"),
    )
    op.create_index("ix_webhooks_tenant_id", "webhooks", ["tenant_id"])
    op.create_index("ix_webhooks_api_key", "webhooks", ["api_key"])

    # Migrate each tenant's existing single key into a "Default" webhook.
    op.execute(
        "INSERT INTO webhooks (tenant_id, name, api_key) "
        "SELECT id, 'Default', webhook_api_key FROM tenants "
        "WHERE webhook_api_key IS NOT NULL"
    )

    op.drop_index("ix_tenants_webhook_api_key", table_name="tenants")
    op.drop_column("tenants", "webhook_api_key")


def downgrade() -> None:
    op.add_column("tenants", sa.Column("webhook_api_key", sa.String(), nullable=True))
    op.create_index("ix_tenants_webhook_api_key", "tenants", ["webhook_api_key"], unique=True)
    op.execute(
        "UPDATE tenants t SET webhook_api_key = w.api_key "
        "FROM webhooks w WHERE w.tenant_id = t.id"
    )
    op.drop_index("ix_webhooks_api_key", table_name="webhooks")
    op.drop_index("ix_webhooks_tenant_id", table_name="webhooks")
    op.drop_table("webhooks")
