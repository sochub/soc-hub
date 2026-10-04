"""user profile, token version and MFA columns

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
"""
import sqlalchemy as sa
from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("job_title", sa.String(100), nullable=True))
    op.add_column("users", sa.Column("timezone", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("avatar_key", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("mfa_secret_enc", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("require_mfa", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tenants", "require_mfa")
    for c in ("mfa_enabled_at", "mfa_secret_enc", "token_version", "avatar_key", "timezone", "job_title"):
        op.drop_column("users", c)
