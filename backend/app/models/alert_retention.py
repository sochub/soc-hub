from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func
from app.db.base_class import Base

RETENTION_STATUSES = ("pending", "dismissed")  # promoted alerts belong to a case and are never purged


class AlertRetentionPolicy(Base):
    """Delete alerts older than ``retain_days`` that match the policy, once a day. Alerts linked to a case are never
    deleted, so case evidence keeps its alert context. Created disabled: preview first, then enable."""
    __tablename__ = "alert_retention_policies"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    retain_days = Column(Integer, nullable=False)
    statuses = Column(JSON, nullable=False)  # subset of RETENTION_STATUSES
    conditions = Column(JSON, nullable=False, default=list)  # the suppression-rule vocabulary; [] = every alert
    enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_deleted = Column(Integer, nullable=False, default=0, server_default="0")
    last_run_trigger = Column(String(16), nullable=True)  # scheduled | manual
    last_run_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)  # manual runs
    last_error = Column(Text, nullable=True)  # set when the last run failed, cleared by the next success
    total_deleted = Column(Integer, nullable=False, default=0, server_default="0")
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
