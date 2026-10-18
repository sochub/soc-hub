"""case guests: case_guests, guest_accounts, guest_notifications + guest columns

Revision ID: c3d4e5f6a7b8
Revises: a7c1d2e3f4b5
"""
import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "a7c1d2e3f4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("mfa_secret_enc", sa.String(), nullable=True),
        sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_guest_accounts_id", "guest_accounts", ["id"])
    op.create_index("ix_guest_accounts_email", "guest_accounts", ["email"], unique=True)

    op.create_table(
        "case_guests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("permission", sa.String(16), nullable=False, server_default="viewer"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guest_account_id", sa.Integer(), sa.ForeignKey("guest_accounts.id", ondelete="SET NULL"),
                  nullable=True),
        sa.UniqueConstraint("case_id", "email", name="uq_case_guest_email"),
        sa.CheckConstraint("permission IN ('viewer','contributor')", name="ck_case_guest_permission"),
    )
    for col in ("id", "tenant_id", "case_id", "email"):
        op.create_index(f"ix_case_guests_{col}", "case_guests", [col])

    op.create_table(
        "guest_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_guest_id", sa.Integer(), sa.ForeignKey("case_guests.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("summary", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_guest_notifications_id", "guest_notifications", ["id"])
    op.create_index("ix_guest_notifications_case_guest_id", "guest_notifications", ["case_guest_id"])

    op.add_column("timeline_events", sa.Column("shared_with_guests", sa.Boolean(), nullable=False,
                                               server_default=sa.false()))
    op.add_column("timeline_events", sa.Column("guest_id", sa.Integer(),
                  sa.ForeignKey("case_guests.id", ondelete="SET NULL"), nullable=True))
    op.add_column("case_attachments", sa.Column("uploaded_by_guest_id", sa.Integer(),
                  sa.ForeignKey("case_guests.id", ondelete="SET NULL"), nullable=True))
    op.add_column("cases", sa.Column("guest_summary", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("guest_domain_allowlist", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("tenants", sa.Column("guest_require_sso", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tenants", "guest_require_sso")
    op.drop_column("tenants", "guest_domain_allowlist")
    op.drop_column("cases", "guest_summary")
    op.drop_column("case_attachments", "uploaded_by_guest_id")
    op.drop_column("timeline_events", "guest_id")
    op.drop_column("timeline_events", "shared_with_guests")
    op.drop_table("guest_notifications")
    op.drop_table("case_guests")
    op.drop_table("guest_accounts")
