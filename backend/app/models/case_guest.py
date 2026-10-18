from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base_class import Base

GUEST_PERMISSIONS = ("viewer", "contributor")


class GuestAccount(Base):
    """Optional login for a guest email; tenant-independent (one person, grants in many tenants)."""
    __tablename__ = "guest_accounts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(320), nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    mfa_secret_enc = Column(String, nullable=True)
    mfa_enabled_at = Column(DateTime(timezone=True), nullable=True)
    token_version = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CaseGuest(Base):
    """One grant: `email` may see `case_id` with `permission` until `expires_at`."""
    __tablename__ = "case_guests"
    __table_args__ = (UniqueConstraint("case_id", "email", name="uq_case_guest_email"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(320), nullable=False, index=True)
    permission = Column(String(16), nullable=False, default="viewer")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    invited_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    guest_account_id = Column(Integer, ForeignKey("guest_accounts.id", ondelete="SET NULL"), nullable=True)


class GuestNotification(Base):
    """Digest queue row; `sent_at` set once included in an email (or dropped for an inactive grant)."""
    __tablename__ = "guest_notifications"

    id = Column(Integer, primary_key=True, index=True)
    case_guest_id = Column(Integer, ForeignKey("case_guests.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(32), nullable=False)  # shared_entry | status_change
    summary = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    sent_at = Column(DateTime(timezone=True), nullable=True)
