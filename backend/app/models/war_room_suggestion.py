from sqlalchemy import (JSON, Column, DateTime, ForeignKey, Index, Integer, String,
                        UniqueConstraint)
from sqlalchemy.sql import func

from app.db.base_class import Base


class WarRoomSuggestion(Base):
    """A SOC-bot suggestion (artifact or milestone) lifted from a case's Slack war-room channel."""
    __tablename__ = "war_room_suggestions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", "kind", "value_key", name="uq_war_room_suggestion_value"),
        Index("ix_war_room_suggestions_reply", "slack_channel", "reply_ts"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False)  # artifact | milestone
    payload = Column(JSON, nullable=False, default=dict)
    value_key = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending", server_default="pending")  # pending|accepted|dismissed|conflict
    slack_channel = Column(String, nullable=True)
    source_ts = Column(String, nullable=True)
    reply_ts = Column(String, nullable=True)
    permalink = Column(String, nullable=True)
    resolved_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WarRoomMessageSeen(Base):
    """Per-message dedupe for Slack event redelivery (insert-or-skip on (channel, ts))."""
    __tablename__ = "war_room_message_seen"

    slack_channel = Column(String, primary_key=True)
    ts = Column(String, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
