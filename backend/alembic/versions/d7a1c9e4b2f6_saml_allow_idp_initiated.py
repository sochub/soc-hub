"""tenant_sso_configs.saml_allow_idp_initiated: unsolicited SAML responses are refused unless enabled

Revision ID: d7a1c9e4b2f6
Revises: c5e8f2a3b1d0
"""
import sqlalchemy as sa
from alembic import op

revision = "d7a1c9e4b2f6"
down_revision = "c5e8f2a3b1d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_sso_configs", sa.Column("saml_allow_idp_initiated", sa.Boolean(), nullable=False,
                                                  server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tenant_sso_configs", "saml_allow_idp_initiated")
