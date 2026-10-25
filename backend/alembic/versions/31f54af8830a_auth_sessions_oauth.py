"""Auth sessions, OAuth clients, tenant MCP toggle

Revision ID: 31f54af8830a
Revises: 3eda39884bf3
Create Date: 2026-10-25 09:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "31f54af8830a"
down_revision = "3eda39884bf3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("client_name", sa.String(200), nullable=False),
        sa.Column("redirect_uris", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_ip", sa.String(64), nullable=True),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("client_id", sa.String(48), sa.ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=True),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=True),
        sa.Column("amr", sa.String(8), nullable=False),
        sa.Column("sso_tenant_id", sa.Integer(), nullable=True),
        sa.Column("tv", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refresh_hash", sa.String(64), nullable=False),
        sa.Column("prev_refresh_hash", sa.String(64), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_revoked", "auth_sessions", ["user_id", "revoked_at"])
    op.create_index("ix_auth_sessions_tenant_kind", "auth_sessions", ["tenant_id", "kind"])
    op.add_column("tenants", sa.Column("mcp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tenants", "mcp_enabled")
    op.drop_index("ix_auth_sessions_tenant_kind", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_revoked", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("oauth_clients")
