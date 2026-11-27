"""security briefs: sources, items, per-organization AI briefs, per-user read/saved state

Revision ID: a7c9e1f3b5d2
Revises: e6a8c0d2f4b3
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c9e1f3b5d2"
down_revision = "e6a8c0d2f4b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("briefs_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tenants", sa.Column("briefs_disabled_sources", sa.JSON(), nullable=False, server_default="[]"))

    op.create_table(
        "brief_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("format", sa.String(16), nullable=False, server_default="rss"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_brief_sources_tenant_id", "brief_sources", ["tenant_id"])
    # NULL tenant = built-in: UNIQUE(tenant_id, url) alone would let built-ins repeat, hence two partial indexes.
    op.create_index("uq_brief_sources_tenant_url", "brief_sources", ["tenant_id", "url"], unique=True,
                    postgresql_where=sa.text("tenant_id IS NOT NULL"))
    op.create_index("uq_brief_sources_builtin_url", "brief_sources", ["url"], unique=True,
                    postgresql_where=sa.text("tenant_id IS NULL"))

    op.create_table(
        "brief_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("brief_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("guid", sa.String(500), nullable=False),
        sa.Column("url", sa.String(2000), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("cves", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "guid", name="uq_brief_items_source_guid"),
    )
    op.create_index("ix_brief_items_published_at", "brief_items", ["published_at"])

    op.create_table(
        "brief_ai",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("brief_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("what_happened", sa.Text(), nullable=True),
        sa.Column("why_it_matters", sa.Text(), nullable=True),
        sa.Column("lessons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("item_id", "tenant_id", name="uq_brief_ai_item_tenant"),
    )
    op.create_index("ix_brief_ai_tenant_id", "brief_ai", ["tenant_id"])

    op.create_table(
        "brief_user_state",
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("brief_items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("brief_user_state")
    op.drop_table("brief_ai")
    op.drop_table("brief_items")
    op.drop_table("brief_sources")
    op.drop_column("tenants", "briefs_disabled_sources")
    op.drop_column("tenants", "briefs_enabled")
