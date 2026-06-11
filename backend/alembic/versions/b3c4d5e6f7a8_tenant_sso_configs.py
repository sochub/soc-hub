"""per-tenant SAML SSO configuration

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-06-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_sso_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("idp_entity_id", sa.String(), nullable=True),
        sa.Column("idp_sso_url", sa.String(), nullable=True),
        sa.Column("idp_x509_cert", sa.Text(), nullable=True),
        sa.Column("auto_provision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_sso_config_tenant"),
    )
    op.create_index(op.f("ix_tenant_sso_configs_id"), "tenant_sso_configs", ["id"])
    op.create_index(op.f("ix_tenant_sso_configs_tenant_id"), "tenant_sso_configs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_tenant_sso_configs_tenant_id"), table_name="tenant_sso_configs")
    op.drop_index(op.f("ix_tenant_sso_configs_id"), table_name="tenant_sso_configs")
    op.drop_table("tenant_sso_configs")
