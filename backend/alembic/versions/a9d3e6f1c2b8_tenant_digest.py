"""tenants.digest_recipients / digest_last_sent_at: weekly per-client summary email

Revision ID: a9d3e6f1c2b8
Revises: f5c8a2e1d4b7
"""
import sqlalchemy as sa
from alembic import op

revision = "a9d3e6f1c2b8"
down_revision = "f5c8a2e1d4b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("digest_recipients", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("tenants", sa.Column("digest_last_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "digest_last_sent_at")
    op.drop_column("tenants", "digest_recipients")
