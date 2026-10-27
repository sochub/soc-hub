from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func
from app.db.base_class import Base

SUPPRESSION_OPS = ("equals", "not_equals", "contains", "starts_with", "ends_with", "in_cidr")


class SuppressionRule(Base):
    """Alerts matching every condition are stored but auto-dismissed at ingest. A rule that has suppressed
    something cannot be deleted or have its conditions changed, only disabled (its hits stay explainable)."""
    __tablename__ = "suppression_rules"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    reason = Column(Text)
    conditions = Column(JSON, nullable=False)  # [{"field": "source"|"title"|"severity"|"payload.a.b", "op", "value"}]
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    expires_at = Column(DateTime(timezone=True), nullable=True)  # NULL = never
    hit_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_hit_at = Column(DateTime(timezone=True), nullable=True)
    created_from_alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
