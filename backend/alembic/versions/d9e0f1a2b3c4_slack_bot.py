"""Slack bot: responder alert routing, war-room channel and live alert card per case

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-10-22 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("slack_integrations", sa.Column("alert_channel", sa.String(), nullable=True))
    op.add_column("slack_integrations", sa.Column("alert_min_severity", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("slack_channel_id", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("slack_card_channel", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("slack_card_ts", sa.String(), nullable=True))


def downgrade() -> None:
    for col in ("slack_card_ts", "slack_card_channel", "slack_channel_id"):
        op.drop_column("cases", col)
    for col in ("alert_min_severity", "alert_channel"):
        op.drop_column("slack_integrations", col)
