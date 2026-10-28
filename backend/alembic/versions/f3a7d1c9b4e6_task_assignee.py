"""case_tasks.assignee_id: optional person a case task is assigned to

Revision ID: f3a7d1c9b4e6
Revises: e9b5c3d7a2f1
"""
import sqlalchemy as sa
from alembic import op

revision = "f3a7d1c9b4e6"
down_revision = "e9b5c3d7a2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_tasks", sa.Column("assignee_id", sa.Integer(),
                                          sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_case_tasks_assignee_id", "case_tasks", ["assignee_id"])


def downgrade() -> None:
    op.drop_index("ix_case_tasks_assignee_id", table_name="case_tasks")
    op.drop_column("case_tasks", "assignee_id")
