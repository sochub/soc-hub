"""playbook/case tasks link a manual workflow; case_tasks remember their last run

Revision ID: c3f9d2a6e8b1
Revises: b4e8c1f7a3d2
"""
import sqlalchemy as sa
from alembic import op

revision = "c3f9d2a6e8b1"
down_revision = "b4e8c1f7a3d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("playbook_task_templates", sa.Column(
        "workflow_id", sa.Integer(), sa.ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True))
    op.add_column("case_tasks", sa.Column(
        "workflow_id", sa.Integer(), sa.ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True))
    op.add_column("case_tasks", sa.Column(
        "last_run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    op.drop_column("case_tasks", "last_run_id")
    op.drop_column("case_tasks", "workflow_id")
    op.drop_column("playbook_task_templates", "workflow_id")
