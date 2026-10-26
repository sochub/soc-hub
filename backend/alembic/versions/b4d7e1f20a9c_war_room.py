"""war room: listen flag, channel name, naming template, suggestions, message dedupe

Revision ID: b4d7e1f20a9c
Revises: 9e3a4b5c6d7f
"""
import sqlalchemy as sa
from alembic import op

revision = "b4d7e1f20a9c"
down_revision = "9e3a4b5c6d7f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("slack_listen", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("cases", sa.Column("slack_channel_name", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("slack_channel_created_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_cases_slack_channel_id", "cases", ["slack_channel_id"])
    op.add_column("slack_integrations", sa.Column("war_room_name_template", sa.String(), nullable=True))
    op.add_column("slack_integrations", sa.Column("last_message_event_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "war_room_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("value_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("slack_channel", sa.String(), nullable=True),
        sa.Column("source_ts", sa.String(), nullable=True),
        sa.Column("reply_ts", sa.String(), nullable=True),
        sa.Column("permalink", sa.String(), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "case_id", "kind", "value_key", name="uq_war_room_suggestion_value"),
    )
    op.create_index("ix_war_room_suggestions_id", "war_room_suggestions", ["id"])
    op.create_index("ix_war_room_suggestions_tenant_id", "war_room_suggestions", ["tenant_id"])
    op.create_index("ix_war_room_suggestions_case_id", "war_room_suggestions", ["case_id"])
    op.create_index("ix_war_room_suggestions_reply", "war_room_suggestions", ["slack_channel", "reply_ts"])

    op.create_table(
        "war_room_message_seen",
        sa.Column("slack_channel", sa.String(), primary_key=True),
        sa.Column("ts", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_war_room_message_seen_tenant_id", "war_room_message_seen", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("war_room_message_seen")
    op.drop_table("war_room_suggestions")
    op.drop_column("slack_integrations", "last_message_event_at")
    op.drop_column("slack_integrations", "war_room_name_template")
    op.drop_index("ix_cases_slack_channel_id", table_name="cases")
    op.drop_column("cases", "slack_channel_created_at")
    op.drop_column("cases", "slack_channel_name")
    op.drop_column("cases", "slack_listen")
