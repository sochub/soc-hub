from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.db.base_class import Base


class SlackIntegration(Base):
    """A tenant's bring-your-own Slack app. Secrets are Fernet-encrypted (app/utils/crypto.py)."""
    __tablename__ = "slack_integrations"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    team_id = Column(String, nullable=True, index=True)
    bot_token_enc = Column(Text, nullable=False)
    signing_secret_enc = Column(Text, nullable=False)
    # App-level token (xapp-, scope connections:write). When set, Slack talks to us over Socket Mode
    # (app/slack_socket.py) instead of the HTTP request URLs.
    app_token_enc = Column(Text, nullable=True)
    default_channel = Column(String, nullable=True)
    # Responder alerts: cases at or above this severity are posted to alert_channel (or default_channel).
    # NULL severity = alerts off.
    alert_channel = Column(String, nullable=True)
    alert_min_severity = Column(String, nullable=True)
    war_room_name_template = Column(String, nullable=True)
    # Last time Slack delivered a message event for this workspace (SOC-bot health signal).
    last_message_event_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
