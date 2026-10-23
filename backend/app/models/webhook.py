from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.core.security import get_password_hash, verify_password
from app.db.base_class import Base

PREFIX_LEN = 12        # "whk_" + 8 random chars: narrows the lookup to (almost always) one row
LEGACY_PREFIX_LEN = 8  # keys created before Argon2 stored only "whk_" + 4 chars


def hash_webhook_key(key: str) -> str:
    """Argon2id, like user passwords. Salted, so lookup goes through key_prefix + verify_webhook_key."""
    return get_password_hash(key)


def webhook_key_prefix(key: str) -> str:
    return key[:PREFIX_LEN]


def is_legacy_hash(stored: str) -> bool:
    return not stored.startswith("$argon2")


def verify_webhook_key(key: str, stored: str) -> bool:
    """CPU-heavy for Argon2 rows: call it off the event loop."""
    if is_legacy_hash(stored):
        # Legacy SHA-256 webhook hashes are no longer accepted.
        return False
    return verify_password(key, stored)


class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)  # source label, e.g. "Splunk"
    # Only the hash is stored; the key itself is shown once, in the create response.
    key_hash = Column(String(255), unique=True, nullable=False)
    # e.g. "whk_Ab3dEf9h": tells keys apart in the UI and narrows the lookup before Argon2 verify
    key_prefix = Column(String(16), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
