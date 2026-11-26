"""alert_retention_policies: last run trigger, who ran it, and the error when it failed

Revision ID: d5f7b9c1e3a2
Revises: c4e6a8b0d2f1
"""
import sqlalchemy as sa
from alembic import op

revision = "d5f7b9c1e3a2"
down_revision = "c4e6a8b0d2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert_retention_policies", sa.Column("last_run_trigger", sa.String(16)))
    op.add_column("alert_retention_policies", sa.Column(
        "last_run_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    op.add_column("alert_retention_policies", sa.Column("last_error", sa.Text()))


def downgrade() -> None:
    op.drop_column("alert_retention_policies", "last_error")
    op.drop_column("alert_retention_policies", "last_run_by")
    op.drop_column("alert_retention_policies", "last_run_trigger")
