from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any, List

class AuditLogBase(BaseModel):
    entity_type: str
    entity_id: int
    action: str
    changes: Optional[Dict[str, Any]] = None

class AuditLogCreate(AuditLogBase):
    user_id: Optional[int] = None

class AuditLog(AuditLogBase):
    id: int
    user_id: Optional[int]
    created_at: datetime
    category: Optional[str] = "change"

    class Config:
        from_attributes = True


class AuditUser(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None


class AuditItem(BaseModel):
    id: int
    created_at: datetime
    category: str
    action: str
    outcome: Optional[str] = None
    status_code: Optional[int] = None
    method: Optional[str] = None
    route: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    user: Optional[AuditUser] = None
    user_email: Optional[str] = None
    actor: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    duration_ms: Optional[int] = None
    changes: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class AuditPage(BaseModel):
    items: List[AuditItem]
    next_before: Optional[int] = None


class AuditFacets(BaseModel):
    categories: List[str]
    actions: List[str]
    users: List[AuditUser]
    entity_types: List[str]
