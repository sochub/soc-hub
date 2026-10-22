"""Slack Socket Mode: per-tenant app-level token

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-10-22 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("slack_integrations", sa.Column("app_token_enc", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("slack_integrations", "app_token_enc")
