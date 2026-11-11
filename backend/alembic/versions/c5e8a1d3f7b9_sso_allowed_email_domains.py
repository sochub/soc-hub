"""SSO JIT provisioning: per-tenant allowed email domains.

Adds tenant_sso_configs.allowed_email_domains (JSON list, default []). JIT provisioning now only creates
accounts whose email domain is on that list. Existing configs with auto_provision on get an EMPTY list, so
SSO sign-up of NEW users is refused ("domains_unset") until a tenant admin sets the domains in
Settings -> SSO. Existing users keep signing in; only automatic account creation is affected.

Revision ID: c5e8a1d3f7b9
Revises: b9d4f2a7c1e5
Create Date: 2026-11-11
"""
import sqlalchemy as sa
from alembic import op

revision = "c5e8a1d3f7b9"
down_revision = "b9d4f2a7c1e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant_sso_configs", sa.Column("allowed_email_domains", sa.JSON(), nullable=False,
                                                  server_default="[]"))


def downgrade() -> None:
    op.drop_column("tenant_sso_configs", "allowed_email_domains")
