"""store webhook keys as SHA-256 hashes; backfill NULL case tags

Revision ID: f8b2c3d4e5a6
Revises: e7a1b2c3d4f5
Create Date: 2026-10-18 00:00:00.000000

Downgrade cannot restore the keys: every webhook comes back with an unusable placeholder and must be recreated.
"""
from alembic import op
import sqlalchemy as sa

revision = "f8b2c3d4e5a6"
down_revision = "e7a1b2c3d4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("webhooks", sa.Column("key_hash", sa.String(64), nullable=True))
    op.add_column("webhooks", sa.Column("key_prefix", sa.String(16), nullable=True))
    op.execute("UPDATE webhooks SET key_hash = encode(sha256(convert_to(api_key, 'UTF8')), 'hex'), "
               "key_prefix = left(api_key, 8)")
    op.alter_column("webhooks", "key_hash", nullable=False)
    op.alter_column("webhooks", "key_prefix", nullable=False)
    op.create_unique_constraint("uq_webhooks_key_hash", "webhooks", ["key_hash"])
    op.drop_column("webhooks", "api_key")

    # A NULL tags value made GET /cases fail validation for the whole tenant.
    op.execute("UPDATE cases SET tags = '[]' WHERE tags IS NULL OR tags::text = 'null'")
    op.alter_column("cases", "tags", server_default=sa.text("'[]'"))


def downgrade() -> None:
    op.alter_column("cases", "tags", server_default=None)
    op.add_column("webhooks", sa.Column("api_key", sa.String(), nullable=True))
    op.execute("UPDATE webhooks SET api_key = 'revoked-' || key_hash")
    op.alter_column("webhooks", "api_key", nullable=False)
    op.create_unique_constraint("webhooks_api_key_key", "webhooks", ["api_key"])
    op.drop_constraint("uq_webhooks_key_hash", "webhooks")
    op.drop_column("webhooks", "key_prefix")
    op.drop_column("webhooks", "key_hash")
