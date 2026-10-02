from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, field_validator

from app.reports import tlp as tlp_mod


def _check_tlp(v):
    if v is None:
        return v
    n = tlp_mod.normalize(v)
    if n is None:
        raise ValueError("tlp must be one of white/clear, green, amber, red")
    return n


class IOCBase(BaseModel):
    ioc_type: str
    value: str
    threat_level: str = "medium"
    confidence: int = 50
    status: str = "active"
    tlp: str = "amber"
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    source: Optional[str] = None
    tags: List[str] = []
    description: Optional[str] = None
    case_id: Optional[int] = None

    _tlp = field_validator("tlp", mode="before")(_check_tlp)


class IOCCreate(IOCBase):
    pass


class IOCUpdate(BaseModel):
    ioc_type: Optional[str] = None
    value: Optional[str] = None
    threat_level: Optional[str] = None
    confidence: Optional[int] = None
    status: Optional[str] = None
    tlp: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    source: Optional[str] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    case_id: Optional[int] = None

    _tlp = field_validator("tlp", mode="before")(_check_tlp)


class IOC(IOCBase):
    id: int
    tenant_id: int
    created_at: datetime
    created_by: Optional[int] = None
    enrichment_verdict: Optional[str] = None

    # Responses show stored values as they are: legacy non-canonical TLPs must not break reads.
    @field_validator("tlp", mode="before")
    @classmethod
    def _tlp(cls, v):
        return v

    class Config:
        from_attributes = True
