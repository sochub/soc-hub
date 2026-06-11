"""tenant memberships + is_super_admin; drop users.tenant_id/role

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-08 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. global super-admin flag
    op.add_column(
        "users",
        sa.Column("is_super_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE users SET is_super_admin = true WHERE role = 'super_admin'")

    # 2. memberships table
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
    )
    op.create_index(op.f("ix_tenant_memberships_id"), "tenant_memberships", ["id"])
    op.create_index(op.f("ix_tenant_memberships_user_id"), "tenant_memberships", ["user_id"])
    op.create_index(op.f("ix_tenant_memberships_tenant_id"), "tenant_memberships", ["tenant_id"])

    # 3. backfill from existing single-tenant users
    op.execute(
        "INSERT INTO tenant_memberships (user_id, tenant_id, role) "
        "SELECT id, tenant_id, role FROM users "
        "WHERE tenant_id IS NOT NULL AND role != 'super_admin'"
    )

    # 4. drop the old single-tenant columns
    op.drop_column("users", "tenant_id")
    op.drop_column("users", "role")


def downgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(), nullable=True, server_default="analyst"))
    op.add_column("users", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.create_foreign_key("users_tenant_id_fkey", "users", "tenants", ["tenant_id"], ["id"])

    # Restore each user's lowest-tenant membership as their primary tenant/role.
    op.execute(
        "UPDATE users u SET tenant_id = m.tenant_id, role = m.role "
        "FROM (SELECT DISTINCT ON (user_id) user_id, tenant_id, role "
        "      FROM tenant_memberships ORDER BY user_id, tenant_id) m "
        "WHERE u.id = m.user_id"
    )
    op.execute("UPDATE users SET role = 'super_admin' WHERE is_super_admin = true")

    op.drop_index(op.f("ix_tenant_memberships_tenant_id"), table_name="tenant_memberships")
    op.drop_index(op.f("ix_tenant_memberships_user_id"), table_name="tenant_memberships")
    op.drop_index(op.f("ix_tenant_memberships_id"), table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_column("users", "is_super_admin")
