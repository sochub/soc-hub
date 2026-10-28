"""case_tasks.result / result_status / result_at: what an automation found when it completed the task

Revision ID: d8a4b7c2e5f9
Revises: c3f9d2a6e8b1
"""
import sqlalchemy as sa
from alembic import op

revision = "d8a4b7c2e5f9"
down_revision = "c3f9d2a6e8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_tasks", sa.Column("result", sa.Text(), nullable=True))
    op.add_column("case_tasks", sa.Column("result_status", sa.String(16), nullable=True))
    op.add_column("case_tasks", sa.Column("result_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("case_tasks", "result_at")
    op.drop_column("case_tasks", "result_status")
    op.drop_column("case_tasks", "result")
