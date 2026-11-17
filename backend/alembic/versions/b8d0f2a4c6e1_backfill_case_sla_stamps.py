"""backfill case SLA stamps: acknowledged_at / resolved_at for cases created past NEW

Cases created in a status other than NEW never got acknowledged_at (and resolved/closed ones never got
resolved_at), so their response clock ran forever. Fill-NULL only; the stamps are the creation/last-update time,
so the response clock reads "met" and the breach sweep does not email for them.

Revision ID: b8d0f2a4c6e1
Revises: a7c9e1b3d5f2
"""
from alembic import op

revision = "b8d0f2a4c6e1"
down_revision = "a7c9e1b3d5f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # casestatus stores the enum NAMES (upper case).
    op.execute("UPDATE cases SET acknowledged_at = created_at "
               "WHERE status IS NOT NULL AND status <> 'NEW' AND acknowledged_at IS NULL")
    op.execute("UPDATE cases SET resolved_at = COALESCE(updated_at, created_at) "
               "WHERE status IN ('RESOLVED', 'CLOSED') AND resolved_at IS NULL")


def downgrade() -> None:
    # Data-only backfill: the old NULLs were wrong, nothing to restore.
    pass
