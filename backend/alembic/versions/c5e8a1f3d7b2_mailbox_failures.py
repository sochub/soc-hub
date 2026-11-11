"""email intake: per-UID failure counter (mailbox_failures)

Revision ID: c5e8a1f3d7b2
Revises: b9d4f2a7c1e5
"""
import sqlalchemy as sa
from alembic import op

revision = "c5e8a1f3d7b2"
down_revision = "b9d4f2a7c1e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mailbox_failures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mailbox_id", sa.Integer(), sa.ForeignKey("tenant_mailboxes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uid", sa.String(64), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("mailbox_id", "uid", name="uq_mailbox_failures_mailbox_uid"),
    )


def downgrade() -> None:
    op.drop_table("mailbox_failures")
