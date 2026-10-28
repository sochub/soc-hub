from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func
from app.db.base_class import Base

TARGET_KINDS = ("host", "user", "ip")
DEFAULT_DAILY_CAP = 25
DEFAULT_MAX_TARGETS_PER_CASE = 5


class ContainmentPolicy(Base):
    """Guardrails for automated containment (the containment_guard workflow node). No row = the defaults."""
    __tablename__ = "containment_policies"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    daily_cap = Column(Integer, nullable=False, default=DEFAULT_DAILY_CAP)  # actions per tenant per rolling 24 h
    max_targets_per_case = Column(Integer, nullable=False, default=DEFAULT_MAX_TARGETS_PER_CASE)
    protected = Column(JSON, nullable=False, default=list)  # [{"kind": host|user|ip|any, "pattern": str, "note": str}]
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ContainmentAction(Base):
    """Ledger of automated containment: what was contained (or refused by policy), where, and its release."""
    __tablename__ = "containment_actions"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id = Column(Integer, ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)
    action = Column(String, nullable=False)  # e.g. "Contain host (CrowdStrike)"
    target_kind = Column(String, nullable=False)
    target = Column(String, nullable=False)
    status = Column(String, nullable=False)  # active | released | blocked
    reason = Column(Text, nullable=True)  # why it was blocked
    release_hint = Column(Text, nullable=True)  # how to undo it at the vendor
    released_at = Column(DateTime(timezone=True), nullable=True)
    released_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
