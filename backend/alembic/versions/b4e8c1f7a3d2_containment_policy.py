"""containment_policies + containment_actions: guardrails and ledger for automated containment

Revision ID: b4e8c1f7a3d2
Revises: a9d3e6f1c2b8
"""
import sqlalchemy as sa
from alembic import op

revision = "b4e8c1f7a3d2"
down_revision = "a9d3e6f1c2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "containment_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
                  unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column("max_targets_per_case", sa.Integer(), nullable=False),
        sa.Column("protected", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_containment_policies_id", "containment_policies", ["id"])
    op.create_table(
        "containment_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE")),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="SET NULL")),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflows.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_kind", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("release_hint", sa.Text()),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("released_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for col in ("id", "tenant_id", "case_id", "created_at"):
        op.create_index(f"ix_containment_actions_{col}", "containment_actions", [col])


def downgrade() -> None:
    for col in ("created_at", "case_id", "tenant_id", "id"):
        op.drop_index(f"ix_containment_actions_{col}", table_name="containment_actions")
    op.drop_table("containment_actions")
    op.drop_index("ix_containment_policies_id", table_name="containment_policies")
    op.drop_table("containment_policies")
