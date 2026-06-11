"""add per-tenant webhook api key

Revision ID: a7b8c9d0e1f2
Revises: f3g4h5i6j7k8
Create Date: 2026-06-08 00:00:00.000000
"""
import secrets

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e1f2"
down_revision = "f3g4h5i6j7k8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("webhook_api_key", sa.String(), nullable=True),
    )

    # Backfill existing tenants with their own unique key so the webhook keeps
    # working after the shared-key endpoint is removed.
    tenants = sa.table(
        "tenants",
        sa.column("id", sa.Integer),
        sa.column("webhook_api_key", sa.String),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.select(tenants.c.id)).fetchall()
    for (tenant_id,) in rows:
        conn.execute(
            tenants.update()
            .where(tenants.c.id == tenant_id)
            .values(webhook_api_key=f"whk_{secrets.token_urlsafe(32)}")
        )

    op.create_index(
        op.f("ix_tenants_webhook_api_key"),
        "tenants",
        ["webhook_api_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tenants_webhook_api_key"), table_name="tenants")
    op.drop_column("tenants", "webhook_api_key")
