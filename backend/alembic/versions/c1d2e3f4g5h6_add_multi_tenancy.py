"""add multi-tenancy support

Revision ID: c1d2e3f4g5h6
Revises: b1c2d3e4f5g6
Create Date: 2026-02-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4g5h6"
down_revision: Union[str, None] = "b1c2d3e4f5g6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create tenants table
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tenants_id", "tenants", ["id"])
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # 2. Insert default tenant and fix sequence
    op.execute("INSERT INTO tenants (id, name, slug) VALUES (1, 'Default', 'default')")
    op.execute("SELECT setval(pg_get_serial_sequence('tenants', 'id'), (SELECT MAX(id) FROM tenants))")

    # 3. Normalize userrole enum values to lowercase and add super_admin
    op.execute("ALTER TYPE userrole RENAME VALUE 'ADMIN' TO 'admin'")
    op.execute("ALTER TYPE userrole RENAME VALUE 'ANALYST' TO 'analyst'")
    op.execute("ALTER TYPE userrole RENAME VALUE 'VIEWER' TO 'viewer'")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'super_admin'")

    # 4. Add tenant_id to users (nullable for SUPER_ADMIN)
    op.add_column("users", sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True))
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    # Backfill existing users to default tenant
    op.execute("UPDATE users SET tenant_id = 1")

    # 5. Add tenant_id to cases (NOT NULL)
    op.add_column("cases", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE cases SET tenant_id = 1")
    op.alter_column("cases", "tenant_id", nullable=False)
    op.create_foreign_key("fk_cases_tenant_id", "cases", "tenants", ["tenant_id"], ["id"])
    op.create_index("ix_cases_tenant_id", "cases", ["tenant_id"])

    # 6. Add tenant_id to alerts (NOT NULL)
    op.add_column("alerts", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE alerts SET tenant_id = 1")
    op.alter_column("alerts", "tenant_id", nullable=False)
    op.create_foreign_key("fk_alerts_tenant_id", "alerts", "tenants", ["tenant_id"], ["id"])
    op.create_index("ix_alerts_tenant_id", "alerts", ["tenant_id"])

    # 7. Add tenant_id to case_links (NOT NULL)
    op.add_column("case_links", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE case_links SET tenant_id = 1")
    op.alter_column("case_links", "tenant_id", nullable=False)
    op.create_foreign_key("fk_case_links_tenant_id", "case_links", "tenants", ["tenant_id"], ["id"])
    op.create_index("ix_case_links_tenant_id", "case_links", ["tenant_id"])

    # 8. Add tenant_id to artifacts (NOT NULL)
    op.add_column("artifacts", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE artifacts SET tenant_id = 1")
    op.alter_column("artifacts", "tenant_id", nullable=False)
    op.create_foreign_key("fk_artifacts_tenant_id", "artifacts", "tenants", ["tenant_id"], ["id"])
    op.create_index("ix_artifacts_tenant_id", "artifacts", ["tenant_id"])

    # 9. Add tenant_id to audit_logs (NOT NULL)
    op.add_column("audit_logs", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE audit_logs SET tenant_id = 1")
    op.alter_column("audit_logs", "tenant_id", nullable=False)
    op.create_foreign_key("fk_audit_logs_tenant_id", "audit_logs", "tenants", ["tenant_id"], ["id"])
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"])

    # 10. Create invitations table
    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(), nullable=False, index=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("role", sa.String(), nullable=False, server_default="analyst"),
        sa.Column("token", sa.String(), nullable=False, unique=True, index=True),
        sa.Column("status", sa.Enum("pending", "accepted", "expired", "revoked", name="invitationstatus"), server_default="pending"),
        sa.Column("invited_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invitations_id", "invitations", ["id"])


def downgrade() -> None:
    op.drop_table("invitations")
    op.execute("DROP TYPE IF EXISTS invitationstatus")

    op.drop_index("ix_audit_logs_tenant_id", table_name="audit_logs")
    op.drop_constraint("fk_audit_logs_tenant_id", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "tenant_id")

    op.drop_index("ix_artifacts_tenant_id", table_name="artifacts")
    op.drop_constraint("fk_artifacts_tenant_id", "artifacts", type_="foreignkey")
    op.drop_column("artifacts", "tenant_id")

    op.drop_index("ix_case_links_tenant_id", table_name="case_links")
    op.drop_constraint("fk_case_links_tenant_id", "case_links", type_="foreignkey")
    op.drop_column("case_links", "tenant_id")

    op.drop_index("ix_alerts_tenant_id", table_name="alerts")
    op.drop_constraint("fk_alerts_tenant_id", "alerts", type_="foreignkey")
    op.drop_column("alerts", "tenant_id")

    op.drop_index("ix_cases_tenant_id", table_name="cases")
    op.drop_constraint("fk_cases_tenant_id", "cases", type_="foreignkey")
    op.drop_column("cases", "tenant_id")

    op.drop_index("ix_users_tenant_id", table_name="users")
    op.drop_column("users", "tenant_id")

    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_index("ix_tenants_id", table_name="tenants")
    op.drop_table("tenants")
