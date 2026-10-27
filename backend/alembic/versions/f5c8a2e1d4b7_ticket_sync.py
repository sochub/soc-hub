"""ticket_connections + case_links sync columns: per-tenant two-way Jira / HaloPSA / ConnectWise sync

Revision ID: f5c8a2e1d4b7
Revises: e2b7c4d9a1f3
"""
import sqlalchemy as sa
from alembic import op

revision = "f5c8a2e1d4b7"
down_revision = "e2b7c4d9a1f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
                  unique=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("credentials_enc", sa.Text(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_create_min_severity", sa.String()),
        sa.Column("auto_create_since", sa.DateTime(timezone=True)),
        sa.Column("push_notes", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_ticket_connections_id", "ticket_connections", ["id"])
    # a case's links go with it
    op.drop_constraint("case_links_case_id_fkey", "case_links", type_="foreignkey")
    op.create_foreign_key("case_links_case_id_fkey", "case_links", "cases", ["case_id"], ["id"], ondelete="CASCADE")
    op.add_column("case_links", sa.Column("connection_id", sa.Integer(),
                                          sa.ForeignKey("ticket_connections.id", ondelete="SET NULL")))
    op.create_index("ix_case_links_connection_id", "case_links", ["connection_id"])
    op.add_column("case_links", sa.Column("remote_status", sa.String()))
    op.add_column("case_links", sa.Column("remote_closed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("case_links", sa.Column("remote_cursor", sa.String()))
    op.add_column("case_links", sa.Column("pushed_note_id", sa.Integer()))
    op.add_column("case_links", sa.Column("pushed_case_status", sa.String()))
    op.add_column("case_links", sa.Column("last_synced_at", sa.DateTime(timezone=True)))
    op.add_column("case_links", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))


def downgrade() -> None:
    for col in ("created_at", "last_synced_at", "pushed_case_status", "pushed_note_id", "remote_cursor",
                "remote_closed", "remote_status"):
        op.drop_column("case_links", col)
    op.drop_index("ix_case_links_connection_id", table_name="case_links")
    op.drop_column("case_links", "connection_id")
    op.drop_constraint("case_links_case_id_fkey", "case_links", type_="foreignkey")
    op.create_foreign_key("case_links_case_id_fkey", "case_links", "cases", ["case_id"], ["id"])
    op.drop_index("ix_ticket_connections_id", table_name="ticket_connections")
    op.drop_table("ticket_connections")
