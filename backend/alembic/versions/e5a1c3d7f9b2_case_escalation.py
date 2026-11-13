"""case escalation: groups, tiers, escalations, integrations

Revision ID: e5a1c3d7f9b2
Revises: d7f2b4c6e8a1
"""
import sqlalchemy as sa
from alembic import op

revision = "e5a1c3d7f9b2"
down_revision = "d7f2b4c6e8a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_user_group_tenant_name"),
    )
    op.create_table(
        "user_group_members",
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("membership_id", sa.Integer(), sa.ForeignKey("tenant_memberships.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "escalation_tiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("accept_timeout_minutes", sa.Integer(), nullable=True),
        sa.Column("notify_email", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notify_slack", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("slack_channel", sa.String(80), nullable=True),
    )
    op.create_table(
        "escalation_tier_groups",
        sa.Column("tier_id", sa.Integer(), sa.ForeignKey("escalation_tiers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "case_escalations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_tier_id", sa.Integer(), sa.ForeignKey("escalation_tiers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_tier_id", sa.Integer(), sa.ForeignKey("escalation_tiers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("from_group_id", sa.Integer(), sa.ForeignKey("user_groups.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_group_id", sa.Integer(), sa.ForeignKey("user_groups.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("requested_help", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("accepted_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_via", sa.String(16), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_note", sa.Text(), nullable=True),
        sa.Column("overdue_notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_case_escalations_case_id", "case_escalations", ["case_id"])
    op.create_index("ix_case_escalations_status", "case_escalations", ["status"])
    op.create_index("ix_case_escalations_due_at", "case_escalations", ["due_at"])
    op.create_table(
        "escalation_integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("inbound_key", sa.String(64), nullable=False, unique=True),
        sa.Column("pagerduty_webhook_secret_enc", sa.Text(), nullable=True),
        sa.Column("pagerduty_api_key_enc", sa.Text(), nullable=True),
        sa.Column("opsgenie_token_enc", sa.Text(), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("escalation_id", sa.Integer(), sa.ForeignKey("case_escalations.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notifications", "escalation_id")
    op.drop_table("escalation_integrations")
    op.drop_index("ix_case_escalations_due_at", table_name="case_escalations")
    op.drop_index("ix_case_escalations_status", table_name="case_escalations")
    op.drop_index("ix_case_escalations_case_id", table_name="case_escalations")
    op.drop_table("case_escalations")
    op.drop_table("escalation_tier_groups")
    op.drop_table("escalation_tiers")
    op.drop_table("user_group_members")
    op.drop_table("user_groups")
