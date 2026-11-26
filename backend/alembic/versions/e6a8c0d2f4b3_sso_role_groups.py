"""tenant_sso_configs.role_groups: member role from IdP groups at each SSO sign-in

Revision ID: e6a8c0d2f4b3
Revises: b8d0f2a4c6e1
"""
import sqlalchemy as sa
from alembic import op

revision = "e6a8c0d2f4b3"
down_revision = "b8d0f2a4c6e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_sso_configs", sa.Column("role_groups", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("tenant_sso_configs", "role_groups")
