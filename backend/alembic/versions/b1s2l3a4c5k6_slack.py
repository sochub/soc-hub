"""slack_integrations

Revision ID: b1s2l3a4c5k6
Revises: a1w2f3l4o5w6
"""
import sqlalchemy as sa
from alembic import op

revision = "b1s2l3a4c5k6"
down_revision = "a1w2f3l4o5w6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "slack_integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("team_id", sa.String()),
        sa.Column("bot_token_enc", sa.Text(), nullable=False),
        sa.Column("signing_secret_enc", sa.Text(), nullable=False),
        sa.Column("default_channel", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_slack_integrations_team_id", "slack_integrations", ["team_id"])


def downgrade():
    op.drop_table("slack_integrations")
