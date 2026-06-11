from sqlalchemy import Boolean, Column, Integer, String, Enum, ForeignKey, DateTime, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base
import enum
import datetime

class CaseStatus(str, enum.Enum):
    NEW = "new"
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"

class CaseSeverity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    description = Column(Text)
    status = Column(Enum(CaseStatus), default=CaseStatus.NEW, index=True)
    severity = Column(Enum(CaseSeverity), default=CaseSeverity.MEDIUM, index=True)

    tags = Column(JSON, default=list)
    source = Column(String, default="user-reported", index=True)

    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    playbook_template_id = Column(Integer, ForeignKey("playbook_templates.id", ondelete="SET NULL"), nullable=True)
    owner = relationship("User")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    alerts = relationship("Alert", back_populates="case")
    timeline_events = relationship("TimelineEvent", back_populates="case")
    case_artifacts = relationship("CaseArtifact", back_populates="case", cascade="all, delete-orphan")
    links = relationship("CaseLink", back_populates="case")
    iocs = relationship("IOC", back_populates="case")
    tasks = relationship("CaseTask", back_populates="case", cascade="all, delete-orphan")

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, index=True)  # e.g., "EDR", "SIEM"
    external_id = Column(String, index=True)
    title = Column(String)
    payload = Column(JSON)  # Raw alert data
    status = Column(String, default="pending")  # pending, promoted, dismissed

    case_id = Column(Integer, ForeignKey("cases.id"), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    case = relationship("Case", back_populates="alerts")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # System events may have no user
    event_type = Column(String)  # comment, status_change, artifact_added, etc.
    content = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    case = relationship("Case", back_populates="timeline_events")
    user = relationship("User")

class CaseLink(Base):
    __tablename__ = "case_links"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    system = Column(String)  # e.g., "Jira", "ServiceNow"
    external_id = Column(String) # e.g., "SEC-123"
    url = Column(String)

    case = relationship("Case", back_populates="links")
