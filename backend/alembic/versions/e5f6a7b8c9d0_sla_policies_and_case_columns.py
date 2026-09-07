"""sla policies + case sla tracking columns

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-07 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sla_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("response_target_minutes", sa.Integer(), nullable=True),
        sa.Column("resolution_target_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "severity", name="uq_sla_policy_tenant_severity"),
    )
    op.create_index(op.f("ix_sla_policies_id"), "sla_policies", ["id"])
    op.create_index(op.f("ix_sla_policies_tenant_id"), "sla_policies", ["tenant_id"])

    op.add_column("cases", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cases", sa.Column("sla_response_breach_notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cases", sa.Column("sla_resolution_breach_notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "sla_resolution_breach_notified_at")
    op.drop_column("cases", "sla_response_breach_notified_at")
    op.drop_column("cases", "acknowledged_at")

    op.drop_index(op.f("ix_sla_policies_tenant_id"), table_name="sla_policies")
    op.drop_index(op.f("ix_sla_policies_id"), table_name="sla_policies")
    op.drop_table("sla_policies")
