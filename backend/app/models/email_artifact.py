from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.db.base_class import Base


class EmailArtifact(Base):
    """A stored email (kind='eml') or one of its attachments (kind='attachment') from email intake."""
    __tablename__ = "email_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    mailbox_id = Column(Integer, ForeignKey("tenant_mailboxes.id", ondelete="SET NULL"), nullable=True)
    kind = Column(String(10), nullable=False)  # 'eml' | 'attachment'
    filename = Column(String(255), nullable=False)
    content_type = Column(String(255), nullable=True)
    size_bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    storage_key = Column(String, nullable=False, unique=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True)
    case_attachment_id = Column(Integer, ForeignKey("case_attachments.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
