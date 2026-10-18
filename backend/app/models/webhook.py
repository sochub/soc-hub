import hashlib

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.base_class import Base


def hash_webhook_key(key: str) -> str:
    """Keys are 256-bit random, so a plain SHA-256 is enough (no salt/KDF needed) and keeps lookup indexed."""
    return hashlib.sha256(key.encode()).hexdigest()


class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)  # source label, e.g. "Splunk"
    # Only the hash is stored; the key itself is shown once, in the create response.
    key_hash = Column(String(64), unique=True, nullable=False)
    key_prefix = Column(String(16), nullable=False)  # e.g. "whk_Ab3d", to tell keys apart in the UI
    created_at = Column(DateTime(timezone=True), server_default=func.now())
