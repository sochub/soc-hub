import enum
from sqlalchemy import Column, Integer, String, Enum, ForeignKey, DateTime, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base


class IOCType(str, enum.Enum):
    IP_ADDRESS = "ip_address"
    DOMAIN = "domain"
    URL = "url"
    FILE_HASH = "file_hash"
    EMAIL = "email"
    REGISTRY_KEY = "registry_key"
    MUTEX = "mutex"
    USER_AGENT = "user_agent"
    OTHER = "other"


class ThreatLevel(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IOCStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    WHITELISTED = "whitelisted"
    FALSE_POSITIVE = "false_positive"


class TLPLevel(str, enum.Enum):
    WHITE = "white"
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class IOC(Base):
    __tablename__ = "iocs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=True, index=True)

    ioc_type = Column(String, nullable=False, index=True)
    value = Column(String, nullable=False, index=True)
    threat_level = Column(String, default=ThreatLevel.MEDIUM.value)
    confidence = Column(Integer, default=50)  # 0-100
    status = Column(String, default=IOCStatus.ACTIVE.value, index=True)
    tlp = Column(String, default=TLPLevel.AMBER.value)

    first_seen = Column(DateTime(timezone=True), nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    source = Column(String, nullable=True)
    tags = Column(JSON, default=list)
    description = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    tenant = relationship("Tenant")
    case = relationship("Case", back_populates="iocs")
    creator = relationship("User", foreign_keys=[created_by])
