"""Playbook drafts: status + created_by

Revision ID: 409954f66719
Revises: d8bcd47a88ae
Create Date: 2026-10-25 13:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "409954f66719"
down_revision = "d8bcd47a88ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("playbook_templates", sa.Column("status", sa.String(16), nullable=False, server_default="published"))
    op.add_column("playbook_templates", sa.Column(
        "created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    op.drop_column("playbook_templates", "created_by")
    op.drop_column("playbook_templates", "status")
