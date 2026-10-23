"""Webhook keys: room for Argon2 hashes, index the lookup prefix

Revision ID: 1a2b3c4d5e6f
Revises: e0f1a2b3c4d5
Create Date: 2026-10-23 14:00:00.000000

Existing SHA-256 hashes can't be converted without the plaintext key and are no longer accepted:
those webhooks show "needs rotation" in Settings > Integrations until an admin rotates their key.
"""
from alembic import op
import sqlalchemy as sa

revision = "1a2b3c4d5e6f"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("webhooks", "key_hash", type_=sa.String(255), existing_nullable=False)
    op.create_index("ix_webhooks_key_prefix", "webhooks", ["key_prefix"])


def downgrade() -> None:
    # Argon2 rows don't fit in 64 chars and their keys stop working; rotate them after downgrading.
    op.drop_index("ix_webhooks_key_prefix", table_name="webhooks")
    op.execute("UPDATE webhooks SET key_hash = left(key_hash, 64) WHERE key_hash LIKE '$argon2%'")
    op.alter_column("webhooks", "key_hash", type_=sa.String(64), existing_nullable=False)
