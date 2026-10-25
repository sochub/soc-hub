"""Tenant login methods and OIDC config columns

Revision ID: 3eda39884bf3
Revises: 1a2b3c4d5e6f
Create Date: 2026-10-24 10:00:00.000000

Adds tenants.member_login_method / guest_login_method (password|saml|oidc) and OIDC
columns on tenant_sso_configs. Backfills from the legacy flags
(tenant_sso_configs.enabled, tenants.guest_require_sso), which stay as columns.
"""
from alembic import op
import sqlalchemy as sa

revision = "3eda39884bf3"
down_revision = "1a2b3c4d5e6f"
branch_labels = None
depends_on = None


def backfill_values(guest_require_sso: bool, saml_enabled: bool) -> tuple[str, str]:
    """Return (member_login_method, guest_login_method); the UPDATEs in upgrade() mirror this."""
    return ("saml" if saml_enabled else "password", "saml" if guest_require_sso else "password")


def upgrade() -> None:
    op.add_column("tenants", sa.Column("member_login_method", sa.String(), nullable=False, server_default="password"))
    op.add_column("tenants", sa.Column("guest_login_method", sa.String(), nullable=False, server_default="password"))
    op.add_column("tenant_sso_configs", sa.Column("oidc_issuer", sa.String(), nullable=True))
    op.add_column("tenant_sso_configs", sa.Column("oidc_client_id", sa.String(), nullable=True))
    op.add_column("tenant_sso_configs", sa.Column("oidc_client_secret_enc", sa.Text(), nullable=True))
    op.add_column("tenant_sso_configs", sa.Column(
        "oidc_scopes", sa.String(), nullable=False, server_default="openid email profile"))
    op.execute(
        "UPDATE tenants SET member_login_method = 'saml' "
        "FROM tenant_sso_configs c WHERE c.tenant_id = tenants.id AND c.enabled IS TRUE"
    )
    op.execute("UPDATE tenants SET guest_login_method = 'saml' WHERE guest_require_sso IS TRUE")


def downgrade() -> None:
    for col in ("oidc_scopes", "oidc_client_secret_enc", "oidc_client_id", "oidc_issuer"):
        op.drop_column("tenant_sso_configs", col)
    op.drop_column("tenants", "guest_login_method")
    op.drop_column("tenants", "member_login_method")
