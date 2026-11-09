"""case_attachments.is_inline: images pasted into text fields, hidden from the attachment list

Revision ID: b7c1e4a9d2f6
Revises: f3a7d1c9b4e6
"""
import sqlalchemy as sa
from alembic import op

revision = "b7c1e4a9d2f6"
down_revision = "f3a7d1c9b4e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("case_attachments", sa.Column("is_inline", sa.Boolean(), nullable=False,
                                                server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("case_attachments", "is_inline")
