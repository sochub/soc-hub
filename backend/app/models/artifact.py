from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base
import enum

class ArtifactType(str, enum.Enum):
    IP = "ip"
    DOMAIN = "domain"
    URL = "url"
    FILE_HASH = "file_hash"
    EMAIL = "email"
    OTHER = "other"

class Artifact(Base):
    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    artifact_type = Column(Enum(ArtifactType), default=ArtifactType.OTHER)
    value = Column(String, nullable=False, index=True)
    description = Column(String)
    isolated = Column(Boolean, default=False, nullable=False)
    custom_type_id = Column(Integer, ForeignKey("artifact_type_definitions.id", ondelete="RESTRICT"), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    case_artifacts = relationship("CaseArtifact", back_populates="artifact", cascade="all, delete-orphan")
    user = relationship("User")
    custom_type = relationship("ArtifactTypeDefinition", lazy="joined")

    @property
    def type_key(self) -> str:
        """Effective type: the custom key when custom, else the built-in value."""
        if self.custom_type is not None:
            return self.custom_type.key
        return getattr(self.artifact_type, "value", self.artifact_type)

    @property
    def is_private(self) -> bool:
        return bool(self.custom_type is not None and self.custom_type.private)
