"""alert_retention_policies: scheduled deletion of old alerts that no case holds

Revision ID: c4e6a8b0d2f1
Revises: b8d0f2a4c6e1
"""
import sqlalchemy as sa
from alembic import op

revision = "c4e6a8b0d2f1"
down_revision = "b8d0f2a4c6e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_retention_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("retain_days", sa.Integer(), nullable=False),
        sa.Column("statuses", sa.JSON(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_alert_retention_policies_id", "alert_retention_policies", ["id"])
    op.create_index("ix_alert_retention_policies_tenant_id", "alert_retention_policies", ["tenant_id"])
    # The purge filters on age; alerts had no created_at index.
    op.create_index("ix_alerts_tenant_created_at", "alerts", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_alerts_tenant_created_at", table_name="alerts")
    op.drop_table("alert_retention_policies")
