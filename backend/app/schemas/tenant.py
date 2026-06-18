from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class TenantCreate(BaseModel):
    name: str
    slug: str


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    is_active: Optional[bool] = None


class Tenant(BaseModel):
    id: int
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
