"""normalize tenant HTTP allowlists: legacy entries (URLs, host:port) to bare hostnames, drop invalid ones

Revision ID: c5e8f2a3b1d0
Revises: b4d7e1f20a9c
"""
import json
import logging

import sqlalchemy as sa
from alembic import op

from app.workflows.ssrf import stored_allowlist

revision = "c5e8f2a3b1d0"
down_revision = "b4d7e1f20a9c"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, workflow_http_allowlist FROM tenants")).fetchall()
    for tid, raw in rows:
        hosts = raw if isinstance(raw, list) else json.loads(raw or "[]")
        norm = stored_allowlist(hosts, tid)  # logs each dropped entry (redacted) with the tenant id
        if norm != hosts:
            # never log the raw entries: legacy values may carry userinfo, paths or query strings
            log.warning("http allowlist normalized tenant=%s dropped=%d hosts=%s",
                        tid, len(hosts) - len(norm), norm)
            conn.execute(sa.text("UPDATE tenants SET workflow_http_allowlist = CAST(:v AS JSON) WHERE id = :id"),
                         {"v": json.dumps(norm), "id": tid})


def downgrade() -> None:
    # No-op: the original strings can't be restored, and the normalized form matches the same hosts.
    pass
