from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func
from app.db.base_class import Base

ASSET_TYPES = ("host", "account", "ip", "domain", "service", "other")
COMPROMISE_STATUSES = ("unknown", "compromised", "not_compromised")


class CaseAsset(Base):
    __tablename__ = "case_assets"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    asset_type = Column(String, nullable=False, default="host", server_default="host")
    ip = Column(String)
    domain = Column(String)
    description = Column(Text)
    compromise_status = Column(String, nullable=False, default="unknown", server_default="unknown")
    tags = Column(JSON, default=list)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
