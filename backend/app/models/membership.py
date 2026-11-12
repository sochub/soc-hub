from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base


class TenantMembership(Base):
    """Join row: a user's membership of a tenant, with a per-tenant role.

    role is stored as a plain string ('admin' | 'analyst' | 'viewer') to avoid
    depending on the legacy `userrole` Postgres enum type. super_admin is never
    stored here — it is the global User.is_super_admin flag.
    """
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
        UniqueConstraint("tenant_id", "slack_user_id", name="uq_membership_tenant_slack_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False, default="viewer")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Slack account bound on the first successful Slack action (services/slack_actions._platform_user).
    slack_user_id = Column(String(32), nullable=True)

    user = relationship("User", back_populates="memberships")
    tenant = relationship("Tenant")
