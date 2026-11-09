"""email intake: tenant mailboxes, email artifacts, mailbox dedupe ledger

Revision ID: e4f1b8c2a9d7
Revises: d3e7a9c1b5f2
"""
import sqlalchemy as sa
from alembic import op

revision = "e4f1b8c2a9d7"
down_revision = "d3e7a9c1b5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_mailboxes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("use_ssl", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("folder", sa.String(100), nullable=False, server_default="INBOX"),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("fallback_create_alert", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_tenant_mailboxes_id", "tenant_mailboxes", ["id"])
    op.create_index("ix_tenant_mailboxes_tenant_id", "tenant_mailboxes", ["tenant_id"])

    op.create_table(
        "email_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mailbox_id", sa.Integer(), sa.ForeignKey("tenant_mailboxes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False, unique=True),
        sa.Column("alert_id", sa.Integer(), sa.ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "case_attachment_id",
            sa.Integer(),
            sa.ForeignKey("case_attachments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_email_artifacts_id", "email_artifacts", ["id"])
    op.create_index("ix_email_artifacts_tenant_id", "email_artifacts", ["tenant_id"])
    op.create_index("ix_email_artifacts_sha256", "email_artifacts", ["sha256"])

    op.create_table(
        "mailbox_seen",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "mailbox_id",
            sa.Integer(),
            sa.ForeignKey("tenant_mailboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(512), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("mailbox_id", "message_id", name="uq_mailbox_seen_mailbox_message"),
    )
    op.create_index("ix_mailbox_seen_id", "mailbox_seen", ["id"])


def downgrade() -> None:
    op.drop_index("ix_mailbox_seen_id", table_name="mailbox_seen")
    op.drop_table("mailbox_seen")
    op.drop_index("ix_email_artifacts_sha256", table_name="email_artifacts")
    op.drop_index("ix_email_artifacts_tenant_id", table_name="email_artifacts")
    op.drop_index("ix_email_artifacts_id", table_name="email_artifacts")
    op.drop_table("email_artifacts")
    op.drop_index("ix_tenant_mailboxes_tenant_id", table_name="tenant_mailboxes")
    op.drop_index("ix_tenant_mailboxes_id", table_name="tenant_mailboxes")
    op.drop_table("tenant_mailboxes")
