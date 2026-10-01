from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.db.base_class import Base


class EnrichmentResult(Base):
    __tablename__ = "enrichment_results"
    __table_args__ = (UniqueConstraint("tenant_id", "indicator_type", "indicator_value", "source",
                                       name="uq_enrichment_result"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    indicator_type = Column(String, nullable=False)
    indicator_value = Column(String, nullable=False)
    source = Column(String, nullable=False)
    status = Column(String, nullable=False)
    verdict = Column(String, nullable=True)
    score = Column(String, nullable=True)
    summary = Column(JSON, nullable=True)
    link = Column(String, nullable=True)
    error = Column(String, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
