from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base_class import Base


class TenantMailbox(Base):
    """An IMAP mailbox a tenant polls for email intake. password_enc is Fernet-encrypted (app/utils/crypto.py)."""
    __tablename__ = "tenant_mailboxes"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=993, server_default="993")
    use_ssl = Column(Boolean, nullable=False, default=True, server_default="true")
    username = Column(String(255), nullable=False)
    password_enc = Column(Text, nullable=False)
    folder = Column(String(100), nullable=False, default="INBOX", server_default="INBOX")
    poll_interval_seconds = Column(Integer, nullable=False, default=120, server_default="120")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    fallback_create_alert = Column(Boolean, nullable=False, default=False, server_default="false")
    last_polled_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MailboxSeen(Base):
    """Dedupe ledger: one row per Message-ID already processed for a mailbox."""
    __tablename__ = "mailbox_seen"

    id = Column(Integer, primary_key=True, index=True)
    mailbox_id = Column(Integer, ForeignKey("tenant_mailboxes.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(String(512), nullable=False)
    processed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("mailbox_id", "message_id", name="uq_mailbox_seen_mailbox_message"),
    )


class MailboxFailure(Base):
    """Per-UID failure counter: a message that keeps failing is moved out of the inbox instead of being retried
    forever (it would otherwise hold one of the BATCH_LIMIT slots on every poll)."""
    __tablename__ = "mailbox_failures"

    id = Column(Integer, primary_key=True)
    mailbox_id = Column(Integer, ForeignKey("tenant_mailboxes.id", ondelete="CASCADE"), nullable=False)
    uid = Column(String(64), nullable=False)
    failures = Column(Integer, nullable=False, default=0, server_default="0")
    last_failed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("mailbox_id", "uid", name="uq_mailbox_failures_mailbox_uid"),
    )
