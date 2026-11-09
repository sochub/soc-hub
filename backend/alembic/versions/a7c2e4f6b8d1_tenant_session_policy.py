"""tenants.session_idle_minutes / session_max_minutes: per-organization browser session policy

Revision ID: a7c2e4f6b8d1
Revises: f3a7d1c9b4e6
"""
from alembic import op

revision = "a7c2e4f6b8d1"
down_revision = "f3a7d1c9b4e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: nullable additive columns, safe to re-apply on a DB that got them ahead of this revision.
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS session_idle_minutes INTEGER")
    op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS session_max_minutes INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS session_max_minutes")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS session_idle_minutes")
