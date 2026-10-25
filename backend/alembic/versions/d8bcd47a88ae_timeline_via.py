"""Timeline events: via / via_client attribution

Revision ID: d8bcd47a88ae
Revises: 31f54af8830a
Create Date: 2026-10-25 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "d8bcd47a88ae"
down_revision = "31f54af8830a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("timeline_events", sa.Column("via", sa.String(16), nullable=True))
    op.add_column("timeline_events", sa.Column("via_client", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("timeline_events", "via_client")
    op.drop_column("timeline_events", "via")
