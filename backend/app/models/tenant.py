from sqlalchemy import Boolean, Column, Integer, JSON, LargeBinary, String, Text, DateTime
from sqlalchemy.sql import func, false as sa_false
from app.db.base_class import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    workflow_http_allowlist = Column(JSON, nullable=False, default=list, server_default="[]")
    require_mfa = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    guest_domain_allowlist = Column(JSON, nullable=False, default=list, server_default="[]")
    guest_require_sso = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    # Incident report branding (cover, headers). Logo is a validated PNG/JPEG, <= 256 KB.
    report_org_name = Column(String(120), nullable=True)
    report_accent = Column(String(7), nullable=True)
    report_disclaimer = Column(Text, nullable=True)
    report_logo = Column(LargeBinary, nullable=True)
    report_logo_mime = Column(String(16), nullable=True)
