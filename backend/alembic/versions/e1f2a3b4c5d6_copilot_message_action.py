"""add copilot_messages.action (proposed copilot action)

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("copilot_messages", sa.Column("action", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("copilot_messages", "action")
