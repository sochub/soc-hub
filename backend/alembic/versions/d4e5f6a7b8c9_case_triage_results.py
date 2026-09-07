"""case triage results (AI triage & enrichment)

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-09-06 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_triage_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("triggered_by", sa.String(), nullable=False, server_default="manual"),
        sa.Column("triggered_by_user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("proposed_severity", sa.String(), nullable=True),
        sa.Column("proposed_tags", sa.JSON(), nullable=True),
        sa.Column("severity_tags_status", sa.String(), nullable=False, server_default="proposed"),
        sa.Column("proposed_playbook_template_id", sa.Integer(), nullable=True),
        sa.Column("playbook_status", sa.String(), nullable=False, server_default="proposed"),
        sa.Column("related_case_ids", sa.JSON(), nullable=True),
        sa.Column("next_steps_text", sa.Text(), nullable=True),
        sa.Column("next_steps_status", sa.String(), nullable=False, server_default="proposed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["proposed_playbook_template_id"], ["playbook_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_case_triage_results_id"), "case_triage_results", ["id"])
    op.create_index(op.f("ix_case_triage_results_case_id"), "case_triage_results", ["case_id"])
    op.create_index(op.f("ix_case_triage_results_tenant_id"), "case_triage_results", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_case_triage_results_tenant_id"), table_name="case_triage_results")
    op.drop_index(op.f("ix_case_triage_results_case_id"), table_name="case_triage_results")
    op.drop_index(op.f("ix_case_triage_results_id"), table_name="case_triage_results")
    op.drop_table("case_triage_results")
