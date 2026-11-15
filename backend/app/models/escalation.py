from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.sql import func

from app.db.base_class import Base

ESC_STATUSES = ("pending", "accepted", "redirected", "returned", "climbed", "superseded", "withdrawn", "cancelled")
ACCEPT_VIA = ("soc_hub", "admin_override", "assignment", "pagerduty", "opsgenie")


class UserGroup(Base):
    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_user_group_tenant_name"),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    # IdP group names/ids mapped to this group; non-empty = membership is synced at SSO login
    idp_groups = Column(JSON, nullable=False, default=list, server_default="[]")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserGroupMember(Base):
    __tablename__ = "user_group_members"

    group_id = Column(Integer, ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True)
    membership_id = Column(Integer, ForeignKey("tenant_memberships.id", ondelete="CASCADE"), primary_key=True)


class EscalationTier(Base):
    __tablename__ = "escalation_tiers"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False)
    name = Column(String(100), nullable=False)
    accept_timeout_minutes = Column(Integer, nullable=True)
    notify_email = Column(Boolean, nullable=False, default=False, server_default="false")
    notify_slack = Column(Boolean, nullable=False, default=False, server_default="false")
    slack_channel = Column(String(80), nullable=True)


class EscalationTierGroup(Base):
    __tablename__ = "escalation_tier_groups"

    tier_id = Column(Integer, ForeignKey("escalation_tiers.id", ondelete="CASCADE"), primary_key=True)
    group_id = Column(Integer, ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True)


class CaseEscalation(Base):
    __tablename__ = "case_escalations"
    # At most one pending escalation per case (concurrent escalates: the loser gets 409).
    __table_args__ = (Index("uq_case_escalations_one_pending", "case_id", unique=True,
                            postgresql_where=text("status = 'pending'")),)

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    from_tier_id = Column(Integer, ForeignKey("escalation_tiers.id", ondelete="SET NULL"), nullable=True)
    to_tier_id = Column(Integer, ForeignKey("escalation_tiers.id", ondelete="SET NULL"), nullable=True)
    from_group_id = Column(Integer, ForeignKey("user_groups.id", ondelete="SET NULL"), nullable=True)
    to_group_id = Column(Integer, ForeignKey("user_groups.id", ondelete="SET NULL"), nullable=True)
    requested_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason = Column(Text, nullable=False)
    summary = Column(Text, nullable=False)
    requested_help = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, index=True)
    accepted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_via = Column(String(16), nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    closed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    close_note = Column(Text, nullable=True)
    overdue_notified = Column(Boolean, nullable=False, default=False, server_default="false")
    policy_snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EscalationIntegration(Base):
    __tablename__ = "escalation_integrations"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    inbound_key = Column(String(64), nullable=False, unique=True)
    pagerduty_webhook_secret_enc = Column(Text, nullable=True)
    pagerduty_api_key_enc = Column(Text, nullable=True)
    opsgenie_token_enc = Column(Text, nullable=True)
