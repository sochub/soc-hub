"""platform audit log: categories, request metadata, nullable tenant/entity, FK delete rules

Revision ID: e7a1b2c3d4f5
Revises: c3d4e5f6a7b8
"""
from alembic import op
import sqlalchemy as sa

revision = "e7a1b2c3d4f5"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("audit_logs", sa.Column("category", sa.String(16), nullable=False, server_default="change"))
    for name, typ in [("user_email", sa.String(320)), ("actor", sa.String(128)), ("outcome", sa.String(16)),
                      ("status_code", sa.SmallInteger()), ("method", sa.String(7)), ("route", sa.String(256)),
                      ("ip", sa.String(45)), ("user_agent", sa.String(200)), ("request_id", sa.String(36)),
                      ("duration_ms", sa.Integer())]:
        op.add_column("audit_logs", sa.Column(name, typ, nullable=True))
    op.alter_column("audit_logs", "tenant_id", nullable=True)
    op.alter_column("audit_logs", "entity_type", nullable=True)
    op.alter_column("audit_logs", "entity_id", nullable=True)
    op.drop_constraint("audit_logs_user_id_fkey", "audit_logs", type_="foreignkey")
    op.create_foreign_key("audit_logs_user_id_fkey", "audit_logs", "users", ["user_id"], ["id"], ondelete="SET NULL")
    op.drop_constraint("fk_audit_logs_tenant_id", "audit_logs", type_="foreignkey")
    op.create_foreign_key("fk_audit_logs_tenant_id", "audit_logs", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE")
    op.execute("UPDATE audit_logs a SET user_email = u.email FROM users u WHERE a.user_id = u.id")
    op.create_index("ix_audit_logs_tenant_id_desc", "audit_logs", ["tenant_id", sa.text("id DESC")])
    op.create_index("ix_audit_logs_tenant_category_id", "audit_logs", ["tenant_id", "category", sa.text("id DESC")])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_tenant_created", "audit_logs", ["tenant_id", "created_at"])


def downgrade():
    op.drop_index("ix_audit_logs_tenant_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_request_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_category_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_tenant_id_desc", table_name="audit_logs")
    # Rows without a tenant or entity can't satisfy the old NOT NULLs.
    op.execute("DELETE FROM audit_logs WHERE tenant_id IS NULL OR entity_type IS NULL OR entity_id IS NULL")
    op.drop_constraint("fk_audit_logs_tenant_id", "audit_logs", type_="foreignkey")
    op.create_foreign_key("fk_audit_logs_tenant_id", "audit_logs", "tenants", ["tenant_id"], ["id"])
    op.drop_constraint("audit_logs_user_id_fkey", "audit_logs", type_="foreignkey")
    op.create_foreign_key("audit_logs_user_id_fkey", "audit_logs", "users", ["user_id"], ["id"])
    op.alter_column("audit_logs", "entity_id", nullable=False)
    op.alter_column("audit_logs", "entity_type", nullable=False)
    op.alter_column("audit_logs", "tenant_id", nullable=False)
    for name in ["duration_ms", "request_id", "user_agent", "ip", "route", "method", "status_code",
                 "outcome", "actor", "user_email", "category"]:
        op.drop_column("audit_logs", name)
