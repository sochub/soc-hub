from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.db.base_class import Base


class TenantEnrichmentConfig(Base):
    __tablename__ = "tenant_enrichment_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    auto_max_tlp = Column(String, nullable=False, server_default="green")
    artifact_tlp = Column(String, nullable=False, server_default="amber")
    cache_ttl_hours = Column(Integer, nullable=False, server_default="24")
    sources = Column(JSON, nullable=True)
    vt_per_minute = Column(Integer, nullable=False, server_default="4")
    vt_per_day = Column(Integer, nullable=False, server_default="500")
    internal_domains = Column(JSON, nullable=True)
    credentials_enc = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
