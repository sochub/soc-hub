"""sso group sync: user_groups.idp_groups, tenant_sso_configs.groups_claim

Revision ID: a7c9e1b3d5f2
Revises: e5a1c3d7f9b2
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c9e1b3d5f2"
down_revision = "e5a1c3d7f9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_groups", sa.Column("idp_groups", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("tenant_sso_configs",
                  sa.Column("groups_claim", sa.String(200), nullable=False, server_default="groups"))


def downgrade() -> None:
    op.drop_column("tenant_sso_configs", "groups_claim")
    op.drop_column("user_groups", "idp_groups")
