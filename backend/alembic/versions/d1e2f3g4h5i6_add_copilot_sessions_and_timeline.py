"""add copilot sessions and timeline improvements

Revision ID: d1e2f3g4h5i6
Revises: c1d2e3f4g5h6
Create Date: 2026-02-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d1e2f3g4h5i6"
down_revision: Union[str, None] = "c1d2e3f4g5h6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create copilot_sessions table
    op.create_table(
        "copilot_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("case_id", sa.Integer(), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_copilot_sessions_id", "copilot_sessions", ["id"])
    op.create_index("ix_copilot_sessions_case_id", "copilot_sessions", ["case_id"])
    op.create_index("ix_copilot_sessions_tenant_id", "copilot_sessions", ["tenant_id"])
    op.create_index("ix_copilot_sessions_user_id", "copilot_sessions", ["user_id"])

    # 2. Create copilot_messages table
    op.create_table(
        "copilot_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("copilot_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_copilot_messages_id", "copilot_messages", ["id"])
    op.create_index("ix_copilot_messages_session_id", "copilot_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_copilot_messages_session_id", table_name="copilot_messages")
    op.drop_index("ix_copilot_messages_id", table_name="copilot_messages")
    op.drop_table("copilot_messages")

    op.drop_index("ix_copilot_sessions_user_id", table_name="copilot_sessions")
    op.drop_index("ix_copilot_sessions_tenant_id", table_name="copilot_sessions")
    op.drop_index("ix_copilot_sessions_case_id", table_name="copilot_sessions")
    op.drop_index("ix_copilot_sessions_id", table_name="copilot_sessions")
    op.drop_table("copilot_sessions")
