from pydantic import BaseModel
from datetime import datetime
from typing import Optional, Dict, Any

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

    class Config:
        from_attributes = True
