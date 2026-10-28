"""case_links.task_id: tickets belong to case tasks (one ticket per task)

Revision ID: e9b5c3d7a2f1
Revises: d8a4b7c2e5f9
"""
import sqlalchemy as sa
from alembic import op

revision = "e9b5c3d7a2f1"
down_revision = "d8a4b7c2e5f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_links", sa.Column("task_id", sa.Integer(),
                                          sa.ForeignKey("case_tasks.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_case_links_task_id", "case_links", ["task_id"])
    op.create_index("uq_case_links_task_ticket", "case_links", ["task_id", "connection_id"], unique=True,
                    postgresql_where=sa.text("task_id IS NOT NULL AND connection_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_case_links_task_ticket", table_name="case_links")
    op.drop_index("ix_case_links_task_id", table_name="case_links")
    op.drop_column("case_links", "task_id")
