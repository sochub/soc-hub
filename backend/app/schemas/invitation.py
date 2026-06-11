from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, field_validator
from app.core.passwords import validate_password_strength
from app.models.user import UserRole


class InvitationCreate(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.ANALYST


class InvitationResponse(BaseModel):
    id: int
    email: str
    tenant_id: int
    role: str
    token: str
    status: str
    invited_by: Optional[int] = None
    created_at: datetime
    expires_at: datetime
    invite_link: Optional[str] = None
    # True when an existing user was added straight to the tenant (no token flow).
    added_directly: bool = False

    class Config:
        from_attributes = True


class InvitationAccept(BaseModel):
    token: str
    full_name: str
    password: str

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return validate_password_strength(v)


class InvitationValidation(BaseModel):
    email: str
    tenant_name: str
    role: str
    valid: bool
