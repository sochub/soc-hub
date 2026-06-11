from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class CaseTaskCreate(BaseModel):
    phase: str = "identification"
    title: str
    description: Optional[str] = None


class CaseTaskUpdate(BaseModel):
    phase: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # 'todo' | 'done'


class CaseTaskOut(BaseModel):
    id: int
    case_id: int
    phase: str
    title: str
    description: Optional[str] = None
    status: str
    order: int
    completed_at: Optional[datetime] = None
    completed_by: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True
