from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.sql import func
from app.db.base_class import Base


class TenantSSOConfig(Base):
    """Per-tenant SAML SSO configuration, editable by the tenant's admins.

    The IdP x509 certificate is a public key — not a secret — stored as text.
    """
    __tablename__ = "tenant_sso_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"),
                       nullable=False, unique=True, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    idp_entity_id = Column(String, nullable=True)
    idp_sso_url = Column(String, nullable=True)
    idp_x509_cert = Column(Text, nullable=True)
    # JIT provisioning: create unknown users (and missing memberships) on first
    # SSO login with `default_role` (analyst|viewer — never admin).
    auto_provision = Column(Boolean, nullable=False, default=False)
    default_role = Column(String, nullable=False, default="viewer")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
