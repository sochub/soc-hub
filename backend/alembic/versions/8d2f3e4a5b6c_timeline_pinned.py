"""activity entries can be pinned to the incident glance

Revision ID: 8d2f3e4a5b6c
Revises: 7c1e2d3f4a5b
"""
import sqlalchemy as sa
from alembic import op

revision = "8d2f3e4a5b6c"
down_revision = "7c1e2d3f4a5b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("timeline_events", sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("timeline_events", "pinned")
