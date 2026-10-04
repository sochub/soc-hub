"""notifications and case_followers

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""
import sqlalchemy as sa
from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("timeline_event_id", sa.Integer(), sa.ForeignKey("timeline_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("summary", sa.String(200), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "tenant_id", "read_at"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])
    op.create_table(
        "case_followers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("case_id", "user_id", name="uq_case_follower"),
    )
    op.create_index("ix_case_followers_case_id", "case_followers", ["case_id"])
    op.create_index("ix_case_followers_user_id", "case_followers", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_case_followers_user_id", table_name="case_followers")
    op.drop_index("ix_case_followers_case_id", table_name="case_followers")
    op.drop_table("case_followers")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_table("notifications")
