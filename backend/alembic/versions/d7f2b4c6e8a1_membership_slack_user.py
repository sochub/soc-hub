"""Slack account binding on tenant memberships (trust on first use)

Revision ID: d7f2b4c6e8a1
Revises: c5e8a1f3d7b2
"""
import sqlalchemy as sa
from alembic import op

revision = "d7f2b4c6e8a1"
down_revision = "c5e8a1f3d7b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_memberships", sa.Column("slack_user_id", sa.String(32), nullable=True))
    op.create_unique_constraint("uq_membership_tenant_slack_user", "tenant_memberships", ["tenant_id", "slack_user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_membership_tenant_slack_user", "tenant_memberships", type_="unique")
    op.drop_column("tenant_memberships", "slack_user_id")
