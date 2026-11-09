"""report locks and timeline versions: edit-lock columns on case_reports, updated_at on timeline_events

Revision ID: c8d2f5a1e7b3
Revises: b7c1e4a9d2f6
"""
import sqlalchemy as sa
from alembic import op

revision = "c8d2f5a1e7b3"
down_revision = "b7c1e4a9d2f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("timeline_events", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("case_reports", sa.Column("locked_by", sa.Integer(),
                                            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("case_reports", sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("case_reports", "locked_at")
    op.drop_column("case_reports", "locked_by")
    op.drop_column("timeline_events", "updated_at")
