"""Per-tenant incident report branding

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-10-22 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("report_org_name", sa.String(120), nullable=True))
    op.add_column("tenants", sa.Column("report_accent", sa.String(7), nullable=True))
    op.add_column("tenants", sa.Column("report_disclaimer", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("report_logo", sa.LargeBinary(), nullable=True))
    op.add_column("tenants", sa.Column("report_logo_mime", sa.String(16), nullable=True))


def downgrade() -> None:
    for col in ("report_logo_mime", "report_logo", "report_disclaimer", "report_accent", "report_org_name"):
        op.drop_column("tenants", col)
