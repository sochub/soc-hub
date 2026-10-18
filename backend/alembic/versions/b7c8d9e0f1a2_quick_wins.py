"""Alert severity/owner, case outcome/closing note, scheduled workflows, case assets

Revision ID: b7c8d9e0f1a2
Revises: a9c1e2d3f4b5
Create Date: 2026-10-18 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "b7c8d9e0f1a2"
down_revision = "a9c1e2d3f4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("severity", sa.String(), nullable=False, server_default="medium"))
    op.add_column("alerts", sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.create_index(op.f("ix_alerts_severity"), "alerts", ["severity"])
    op.create_index(op.f("ix_alerts_status"), "alerts", ["status"])

    op.add_column("cases", sa.Column("outcome", sa.String(), nullable=True))
    op.add_column("cases", sa.Column("closing_note", sa.Text(), nullable=True))

    op.add_column("workflows", sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "case_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("asset_type", sa.String(), nullable=False, server_default="host"),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("compromise_status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_case_assets_id"), "case_assets", ["id"])
    op.create_index(op.f("ix_case_assets_case_id"), "case_assets", ["case_id"])
    op.create_index(op.f("ix_case_assets_tenant_id"), "case_assets", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("case_assets")
    op.drop_column("workflows", "last_fired_at")
    op.drop_column("cases", "closing_note")
    op.drop_column("cases", "outcome")
    op.drop_index(op.f("ix_alerts_status"), table_name="alerts")
    op.drop_index(op.f("ix_alerts_severity"), table_name="alerts")
    op.drop_column("alerts", "owner_id")
    op.drop_column("alerts", "severity")
