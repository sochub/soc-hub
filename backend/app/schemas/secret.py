from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.secrets.refs import NAME_RE


class SecretCreate(BaseModel):
    name: str
    value: str
    allowed_hosts: List[str] = Field(default_factory=list, max_length=50)
    description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("name must match ^[A-Z][A-Z0-9_]{1,63}$")
        return v


class SecretUpdate(BaseModel):
    allowed_hosts: Optional[List[str]] = Field(default=None, max_length=50)
    description: Optional[str] = Field(default=None, max_length=500)
    value: Optional[str] = None


class InUseBy(BaseModel):
    id: int
    name: str


class SecretNameOut(BaseModel):
    name: str
    description: Optional[str] = None
    allowed_hosts: List[str] = Field(default_factory=list)


class SecretOut(SecretNameOut):
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by_email: Optional[str] = None
    updated_by_email: Optional[str] = None
    in_use_by: List[InUseBy] = Field(default_factory=list)
