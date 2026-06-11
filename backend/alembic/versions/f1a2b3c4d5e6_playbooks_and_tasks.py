"""playbook templates (marketplace) + case tasks

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-06-09 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playbook_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default="other"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_template_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_template_id"], ["playbook_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_playbook_templates_id"), "playbook_templates", ["id"])
    op.create_index(op.f("ix_playbook_templates_tenant_id"), "playbook_templates", ["tenant_id"])
    op.create_index(op.f("ix_playbook_templates_category"), "playbook_templates", ["category"])

    op.create_table(
        "playbook_task_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False, server_default="identification"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["template_id"], ["playbook_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_playbook_task_templates_id"), "playbook_task_templates", ["id"])
    op.create_index(op.f("ix_playbook_task_templates_template_id"), "playbook_task_templates", ["template_id"])

    op.create_table(
        "case_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False, server_default="identification"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="todo"),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_template_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_template_id"], ["playbook_templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["completed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_case_tasks_id"), "case_tasks", ["id"])
    op.create_index(op.f("ix_case_tasks_case_id"), "case_tasks", ["case_id"])
    op.create_index(op.f("ix_case_tasks_tenant_id"), "case_tasks", ["tenant_id"])

    op.add_column("cases", sa.Column("playbook_template_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "cases_playbook_template_id_fkey", "cases", "playbook_templates",
        ["playbook_template_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("cases_playbook_template_id_fkey", "cases", type_="foreignkey")
    op.drop_column("cases", "playbook_template_id")
    op.drop_table("case_tasks")
    op.drop_table("playbook_task_templates")
    op.drop_table("playbook_templates")
