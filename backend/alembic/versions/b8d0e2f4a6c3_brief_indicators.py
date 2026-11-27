"""security briefs: indicators found in each item, and the per-organization option to match them with observables

Revision ID: b8d0e2f4a6c3
Revises: a7c9e1f3b5d2
"""
import sqlalchemy as sa
from alembic import op

revision = "b8d0e2f4a6c3"
down_revision = "a7c9e1f3b5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL = not computed yet: the detector is Python, so existing rows are filled by the fetch job
    # (app.services.briefs.backfill_indicators), not here. [] = computed, none found.
    op.add_column("brief_items", sa.Column("indicators", sa.JSON(), nullable=True))
    op.add_column("tenants", sa.Column("briefs_correlate", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("tenants", "briefs_correlate")
    op.drop_column("brief_items", "indicators")
