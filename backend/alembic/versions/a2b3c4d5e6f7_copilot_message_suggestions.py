"""add copilot_messages.suggestions (proactive suggested actions)

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("copilot_messages", sa.Column("suggestions", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("copilot_messages", "suggestions")
