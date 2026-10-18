from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Table, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base

# Investigation timeline (what happened in the incident, by event_date), as opposed to
# TimelineEvent, which is the analyst activity log (comments/status changes, by created_at).

# Categories: 3 generic + 12 ATT&CK tactics.
EVENT_CATEGORIES = (
    "unspecified", "legitimate", "remediation",
    "initial_access", "execution", "persistence", "privilege_escalation", "defense_evasion",
    "credential_access", "discovery", "lateral_movement", "collection", "command_and_control",
    "exfiltration", "impact",
)

case_event_artifacts = Table(
    "case_event_artifacts", Base.metadata,
    Column("event_id", Integer, ForeignKey("case_events.id", ondelete="CASCADE"), primary_key=True),
    Column("artifact_id", Integer, ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True),
)


class CaseEvent(Base):
    __tablename__ = "case_events"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    event_date = Column(DateTime(timezone=True), nullable=False, index=True)  # stored UTC, shown in the viewer's tz
    title = Column(String, nullable=False)
    content = Column(Text)   # markdown
    raw = Column(Text)       # verbatim log line / evidence excerpt
    source = Column(String)  # e.g. "EDR", "Sysmon 1", "analyst"
    category = Column(String, nullable=False, default="unspecified", server_default="unspecified")
    tags = Column(JSON, default=list)
    flagged = Column(Boolean, nullable=False, default=False, server_default="false")
    in_summary = Column(Boolean, nullable=False, default=True, server_default="true")  # goes into the report
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    artifacts = relationship("Artifact", secondary=case_event_artifacts, lazy="selectin")
