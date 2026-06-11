from sqlalchemy import Column, Integer, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base


class CaseArtifact(Base):
    __tablename__ = "case_artifacts"
    __table_args__ = (
        UniqueConstraint("case_id", "artifact_id", name="uq_case_artifact"),
    )

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False, index=True)
    artifact_id = Column(Integer, ForeignKey("artifacts.id"), nullable=False, index=True)
    added_at = Column(DateTime(timezone=True), server_default=func.now())
    added_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    case = relationship("Case", back_populates="case_artifacts")
    artifact = relationship("Artifact", back_populates="case_artifacts")
    user = relationship("User")
