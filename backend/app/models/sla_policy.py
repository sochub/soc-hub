from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from app.db.base_class import Base


class SLAPolicy(Base):
    """A tenant's override of the default SLA targets for one severity.

    A missing row for a (tenant, severity) pair means "use the hardcoded
    default" (see app/utils/sla.py) — existing tenants need no backfill.
    NULL target = not tracked for that severity.
    """
    __tablename__ = "sla_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "severity", name="uq_sla_policy_tenant_severity"),
    )

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    severity = Column(String, nullable=False)
    response_target_minutes = Column(Integer, nullable=True)
    resolution_target_minutes = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
