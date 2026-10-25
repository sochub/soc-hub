from sqlalchemy import JSON, Column, DateTime, String
from sqlalchemy.sql import func

from app.db.base_class import Base


class OAuthClient(Base):
    """Dynamically registered public OAuth client (RFC 7591)."""
    __tablename__ = "oauth_clients"

    id = Column(String(48), primary_key=True)
    client_name = Column(String(200), nullable=False)
    redirect_uris = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_ip = Column(String(64), nullable=True)
