from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base_class import Base


class ArtifactTypeDefinition(Base):
    """Tenant-defined artifact type. Artifacts of this type are stored as OTHER + custom_type_id."""
    __tablename__ = "artifact_type_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_artifact_type_tenant_key"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(String(32), nullable=False)
    label = Column(String(64), nullable=False)
    show_in_mindmap = Column(Boolean, nullable=False, default=True, server_default="true")
    private = Column(Boolean, nullable=False, default=False, server_default="false")
    payload_key = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
