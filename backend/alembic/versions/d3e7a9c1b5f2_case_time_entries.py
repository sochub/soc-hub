"""case time entries: per-case time tracking rows

Revision ID: d3e7a9c1b5f2
Revises: c8d2f5a1e7b3
"""
import sqlalchemy as sa
from alembic import op

revision = "d3e7a9c1b5f2"
down_revision = "c8d2f5a1e7b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_time_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(6), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_case_time_entries_id", "case_time_entries", ["id"])
    op.create_index("ix_case_time_entries_tenant_case", "case_time_entries", ["tenant_id", "case_id"])
    op.create_index("ix_case_time_entries_tenant_started", "case_time_entries", ["tenant_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_case_time_entries_tenant_started", table_name="case_time_entries")
    op.drop_index("ix_case_time_entries_tenant_case", table_name="case_time_entries")
    op.drop_index("ix_case_time_entries_id", table_name="case_time_entries")
    op.drop_table("case_time_entries")
