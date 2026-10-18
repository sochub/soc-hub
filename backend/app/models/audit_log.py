from sqlalchemy import Column, Integer, SmallInteger, String, ForeignKey, DateTime, JSON, Index, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base
import enum


class AuditAction(str, enum.Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_id_desc", "tenant_id", text("id DESC")),
        Index("ix_audit_logs_tenant_category_id", "tenant_id", "category", text("id DESC")),
    )

    id = Column(Integer, primary_key=True, index=True)
    # change = detailed before/after rows; write = any mutating request; security = auth events/denials;
    # read = sensitive reads. See docs/audit/README.md.
    category = Column(String(16), nullable=False, server_default="change", default="change")
    entity_type = Column(String, index=True, nullable=True)
    entity_id = Column(Integer, index=True, nullable=True)
    action = Column(String, nullable=False)
    changes = Column(JSON)  # always scrubbed (app.audit.redact.scrub)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_email = Column(String(320), nullable=True)  # snapshot: survives user deletion
    actor = Column(String(128), nullable=True)  # guest:<email> / system / workflow:<id> / webhook:<id>
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    outcome = Column(String(16), nullable=True)
    status_code = Column(SmallInteger, nullable=True)
    method = Column(String(7), nullable=True)
    route = Column(String(256), nullable=True)
    ip = Column(String(45), nullable=True)
    user_agent = Column(String(200), nullable=True)
    request_id = Column(String(36), nullable=True, index=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")
