"""make copilot_sessions.case_id nullable (general sessions)

Revision ID: c9d0e1f2a3b4
Revises: a7b8c9d0e1f2
Create Date: 2026-06-08 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c9d0e1f2a3b4"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("copilot_sessions", "case_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # General (case-less) sessions cannot be represented once the column is NOT
    # NULL again — remove them before tightening the constraint.
    op.execute("DELETE FROM copilot_sessions WHERE case_id IS NULL")
    op.alter_column("copilot_sessions", "case_id", existing_type=sa.Integer(), nullable=False)
