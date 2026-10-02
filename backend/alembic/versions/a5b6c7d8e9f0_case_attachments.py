"""case attachments (evidence files)

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
"""
import sqlalchemy as sa
from alembic import op

revision = "a5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("is_malicious", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_case_attachments_id", "case_attachments", ["id"])
    op.create_index("ix_case_attachments_tenant_id", "case_attachments", ["tenant_id"])
    op.create_index("ix_case_attachments_case_id", "case_attachments", ["case_id"])
    op.create_index("ix_case_attachments_sha256", "case_attachments", ["sha256"])


def downgrade() -> None:
    op.drop_table("case_attachments")
