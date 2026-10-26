"""case-level incident milestones (declared overrides), backfilled from report overrides

Revision ID: 7c1e2d3f4a5b
Revises: 409954f66719
"""
import sqlalchemy as sa
from alembic import op

revision = "7c1e2d3f4a5b"
down_revision = "409954f66719"
branch_labels = None
depends_on = None

_COLS = ("first_seen_at", "detected_at", "contained_at", "recovered_at")


def upgrade() -> None:
    for c in _COLS:
        op.add_column("cases", sa.Column(c, sa.DateTime(timezone=True), nullable=True))
    sets = ", ".join(f"{c} = COALESCE(cases.{c}, r.{c})" for c in _COLS)
    op.execute(f"UPDATE cases SET {sets} FROM case_reports r WHERE r.case_id = cases.id")


def downgrade() -> None:
    for c in reversed(_COLS):
        op.drop_column("cases", c)
