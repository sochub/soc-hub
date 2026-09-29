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
    default_channel = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
