"""hostname and username built-in artifact types

Revision ID: 9e3a4b5c6d7f
Revises: 8d2f3e4a5b6c
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "9e3a4b5c6d7f"
down_revision = "8d2f3e4a5b6c"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")
NEW_KEYS = ("hostname", "username")


def rename_clashing_custom_types(conn) -> None:
    """Tenant custom types whose key now collides with a built-in become `<key>_custom` (artifacts keep their FK)."""
    rows = conn.execute(sa.text(
        "SELECT id, tenant_id, key FROM artifact_type_definitions WHERE key IN ('hostname', 'username')")).fetchall()
    for def_id, tenant_id, key in rows:
        taken = {r[0] for r in conn.execute(
            sa.text("SELECT key FROM artifact_type_definitions WHERE tenant_id = :t"), {"t": tenant_id})}
        new, n = f"{key}_custom", 2
        while new in taken:
            new, n = f"{key}_custom{n}", n + 1
        conn.execute(sa.text("UPDATE artifact_type_definitions SET key = :k WHERE id = :i"), {"k": new, "i": def_id})
        log.warning("renamed custom artifact type %r -> %r (tenant_id=%s)", key, new, tenant_id)


def upgrade() -> None:
    # Postgres cannot use a new enum value in the transaction that adds it; the enum stores member NAMES.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE artifacttype ADD VALUE IF NOT EXISTS 'HOSTNAME'")
        op.execute("ALTER TYPE artifacttype ADD VALUE IF NOT EXISTS 'USERNAME'")
    rename_clashing_custom_types(op.get_bind())


def downgrade() -> None:
    # No-op: enum values cannot be dropped safely, and the renamed custom types are left as they are.
    pass
