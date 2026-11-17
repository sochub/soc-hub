import re
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, field_validator

# New slugs only: existing tenants are read back whatever their slug (see Tenant below).
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SLUG_RULE = "Slug must be 1-64 characters: lowercase letters, digits and hyphens, not starting with a hyphen."


def slug_ok(slug: str) -> bool:
    return bool(SLUG_RE.fullmatch(slug))


class TenantCreate(BaseModel):
    name: str
    slug: str


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name", "slug", "is_active")
    @classmethod
    def _not_null(cls, v):  # omitted = unchanged; an explicit null is refused (422)
        if v is None:
            raise ValueError("may not be null")
        return v


class Tenant(BaseModel):
    id: int
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
