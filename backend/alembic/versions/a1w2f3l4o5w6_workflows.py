"""workflows, runs, steps; case.group_key; alert.dismiss_reason; tenant http allowlist

Revision ID: a1w2f3l4o5w6
Revises: e5f6a7b8c9d0
"""
import sqlalchemy as sa
from alembic import op

revision = "a1w2f3l4o5w6"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenants", sa.Column("workflow_http_allowlist", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("cases", sa.Column("group_key", sa.String(), nullable=True))
    op.create_index("ix_cases_tenant_group_key", "cases", ["tenant_id", "group_key"])
    op.add_column("alerts", sa.Column("dismiss_reason", sa.Text(), nullable=True))

    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trigger_type", sa.String(), nullable=False),
        sa.Column("trigger_filter", sa.Text()),
        sa.Column("graph", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_workflows_tenant_id", "workflows", ["tenant_id"])

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="SET NULL")),
        sa.Column("alert_id", sa.Integer(), sa.ForeignKey("alerts.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("trigger_payload", sa.JSON()),
        sa.Column("graph_snapshot", sa.JSON(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dry_run_mocks", sa.JSON()),
        sa.Column("parent_run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE")),
        sa.Column("parent_step_id", sa.Integer()),
        sa.Column("loop_index", sa.Integer()),
        sa.Column("loop_item", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    for col in ("tenant_id", "workflow_id", "case_id", "alert_id", "status", "parent_run_id", "parent_step_id"):
        op.create_index(f"ix_workflow_runs_{col}", "workflow_runs", [col])

    op.create_table(
        "workflow_run_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("input", sa.JSON()),
        sa.Column("output", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wait_token", sa.String(), unique=True),
        sa.Column("wait_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "node_id", name="uq_run_step_node"),
    )
    op.create_index("ix_workflow_run_steps_run_id", "workflow_run_steps", ["run_id"])
    op.create_index("ix_workflow_run_steps_wait_expires_at", "workflow_run_steps", ["wait_expires_at"])
    op.create_foreign_key("fk_workflow_runs_parent_step", "workflow_runs", "workflow_run_steps",
                          ["parent_step_id"], ["id"], ondelete="CASCADE")


def downgrade():
    op.drop_constraint("fk_workflow_runs_parent_step", "workflow_runs", type_="foreignkey")
    op.drop_table("workflow_run_steps")
    op.drop_table("workflow_runs")
    op.drop_table("workflows")
    op.drop_column("alerts", "dismiss_reason")
    op.drop_index("ix_cases_tenant_group_key", table_name="cases")
    op.drop_column("cases", "group_key")
    op.drop_column("tenants", "workflow_http_allowlist")
