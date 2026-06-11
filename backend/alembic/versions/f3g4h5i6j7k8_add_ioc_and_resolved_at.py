"""add ioc and resolved_at

Revision ID: f3g4h5i6j7k8
Revises: e2f3g4h5i6j7
Create Date: 2026-02-19 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f3g4h5i6j7k8"
down_revision = "e2f3g4h5i6j7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add resolved_at to cases
    op.add_column(
        "cases",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create iocs table
    op.create_table(
        "iocs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("ioc_type", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("threat_level", sa.String(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("tlp", sa.String(), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_iocs_id"), "iocs", ["id"], unique=False)
    op.create_index(op.f("ix_iocs_tenant_id"), "iocs", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_iocs_case_id"), "iocs", ["case_id"], unique=False)
    op.create_index(op.f("ix_iocs_ioc_type"), "iocs", ["ioc_type"], unique=False)
    op.create_index(op.f("ix_iocs_value"), "iocs", ["value"], unique=False)
    op.create_index(op.f("ix_iocs_status"), "iocs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_iocs_status"), table_name="iocs")
    op.drop_index(op.f("ix_iocs_value"), table_name="iocs")
    op.drop_index(op.f("ix_iocs_ioc_type"), table_name="iocs")
    op.drop_index(op.f("ix_iocs_case_id"), table_name="iocs")
    op.drop_index(op.f("ix_iocs_tenant_id"), table_name="iocs")
    op.drop_index(op.f("ix_iocs_id"), table_name="iocs")
    op.drop_table("iocs")
    op.drop_column("cases", "resolved_at")
