"""investigation timeline: case_events + linked artifacts

Revision ID: a9c1e2d3f4b5
Revises: f8b2c3d4e5a6
Create Date: 2026-10-18 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "a9c1e2d3f4b5"
down_revision = "f8b2c3d4e5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("raw", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=False, server_default="unspecified"),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("in_summary", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_case_events_id"), "case_events", ["id"])
    op.create_index(op.f("ix_case_events_case_id"), "case_events", ["case_id"])
    op.create_index(op.f("ix_case_events_tenant_id"), "case_events", ["tenant_id"])
    op.create_index(op.f("ix_case_events_event_date"), "case_events", ["event_date"])
    op.create_table(
        "case_event_artifacts",
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["case_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "artifact_id"),
    )


def downgrade() -> None:
    op.drop_table("case_event_artifacts")
    op.drop_index(op.f("ix_case_events_event_date"), table_name="case_events")
    op.drop_index(op.f("ix_case_events_tenant_id"), table_name="case_events")
    op.drop_index(op.f("ix_case_events_case_id"), table_name="case_events")
    op.drop_index(op.f("ix_case_events_id"), table_name="case_events")
    op.drop_table("case_events")
