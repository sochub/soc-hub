from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.db.base_class import Base


class TenantAIConfig(Base):
    """A tenant's AI-provider override. Secrets live Fernet-encrypted in credentials_enc (JSON)."""
    __tablename__ = "tenant_ai_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    enabled = Column(Boolean, nullable=False, default=True)
    provider = Column(String, nullable=False)
    model = Column(String, nullable=False)
    api_base = Column(String, nullable=True)
    region = Column(String, nullable=True)
    project = Column(String, nullable=True)
    location = Column(String, nullable=True)
    auth_mode = Column(String, nullable=True)
    role_arn = Column(String, nullable=True)
    external_id = Column(String, nullable=True)
    credentials_enc = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
