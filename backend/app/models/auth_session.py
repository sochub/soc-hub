import secrets

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.db.base_class import Base


def new_session_id() -> str:
    return secrets.token_hex(16)


class AuthSession(Base):
    """Server-side login session with a rotating refresh secret (web or oauth)."""
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_revoked", "user_id", "revoked_at"),
        Index("ix_auth_sessions_tenant_kind", "tenant_id", "kind"),
    )

    id = Column(String(32), primary_key=True, default=new_session_id)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(8), nullable=False)  # web | oauth
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    client_id = Column(String(48), ForeignKey("oauth_clients.id", ondelete="CASCADE"), nullable=True)
    resource = Column(String, nullable=True)
    scope = Column(String, nullable=True)
    amr = Column(String(8), nullable=False)
    sso_tenant_id = Column(Integer, nullable=True)
    tv = Column(Integer, nullable=False, default=0, server_default="0")
    refresh_hash = Column(String(64), nullable=False)
    prev_refresh_hash = Column(String(64), nullable=True)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    user_agent = Column(String(300), nullable=True)
    ip = Column(String(64), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
