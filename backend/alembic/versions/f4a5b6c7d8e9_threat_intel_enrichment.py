"""threat intel enrichment tables

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""
import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_enrichment_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("auto_max_tlp", sa.String(), nullable=False, server_default="green"),
        sa.Column("artifact_tlp", sa.String(), nullable=False, server_default="amber"),
        sa.Column("cache_ttl_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("vt_per_minute", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("vt_per_day", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("internal_domains", sa.JSON(), nullable=True),
        sa.Column("credentials_enc", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tenant_enrichment_configs_id", "tenant_enrichment_configs", ["id"])
    op.create_table(
        "enrichment_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("indicator_type", sa.String(), nullable=False),
        sa.Column("indicator_value", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=True),
        sa.Column("score", sa.String(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "indicator_type", "indicator_value", "source", name="uq_enrichment_result"),
    )
    op.create_index("ix_enrichment_results_id", "enrichment_results", ["id"])
    op.create_index("ix_enrichment_results_tenant_id", "enrichment_results", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("enrichment_results")
    op.drop_table("tenant_enrichment_configs")
