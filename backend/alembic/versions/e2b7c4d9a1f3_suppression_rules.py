"""suppression_rules + alerts.suppression_rule_id: expiring, auditable alert suppression at ingest

Revision ID: e2b7c4d9a1f3
Revises: d7a1c9e4b2f6
"""
import sqlalchemy as sa
from alembic import op

revision = "e2b7c4d9a1f3"
down_revision = "d7a1c9e4b2f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppression_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.DateTime(timezone=True)),
        sa.Column("created_from_alert_id", sa.Integer(), sa.ForeignKey("alerts.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_suppression_rules_id", "suppression_rules", ["id"])
    op.create_index("ix_suppression_rules_tenant_id", "suppression_rules", ["tenant_id"])
    op.add_column("alerts", sa.Column("suppression_rule_id", sa.Integer(),
                                      sa.ForeignKey("suppression_rules.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_alerts_suppression_rule_id", "alerts", ["suppression_rule_id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_suppression_rule_id", table_name="alerts")
    op.drop_column("alerts", "suppression_rule_id")
    op.drop_index("ix_suppression_rules_tenant_id", table_name="suppression_rules")
    op.drop_index("ix_suppression_rules_id", table_name="suppression_rules")
    op.drop_table("suppression_rules")
