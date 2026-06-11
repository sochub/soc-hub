from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel


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


class IOC(IOCBase):
    id: int
    tenant_id: int
    created_at: datetime
    created_by: Optional[int] = None

    class Config:
        from_attributes = True
