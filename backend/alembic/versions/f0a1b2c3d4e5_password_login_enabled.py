"""users.password_login_enabled (false for SSO-provisioned users)

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""
import sqlalchemy as sa
from alembic import op

revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_login_enabled", sa.Boolean(), nullable=False,
                                     server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("users", "password_login_enabled")
