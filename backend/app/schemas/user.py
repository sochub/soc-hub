from typing import List, Optional
from zoneinfo import available_timezones
from pydantic import BaseModel, EmailStr, field_validator
from app.core.passwords import validate_password_strength
from app.utils.emails import normalize_email
from app.models.user import UserRole
from app.schemas.membership import MembershipOut


class Token(BaseModel):
    access_token: Optional[str] = None
    token_type: Optional[str] = None
    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_token: Optional[str] = None


class TokenData(BaseModel):
    email: Optional[str] = None
    active_tenant_id: Optional[int] = None


class UserCreate(BaseModel):
    """Create a user (and their membership in the active tenant)."""
    email: EmailStr
    full_name: Optional[str] = None
    role: UserRole = UserRole.ANALYST  # the tenant role to assign
    is_active: bool = True
    password: str
    tenant_id: Optional[int] = None

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v):
        return normalize_email(v) if isinstance(v, str) else v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return validate_password_strength(v)


class UserUpdate(BaseModel):
    """Self-service profile update. Passwords change via POST /users/me/password."""
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    timezone: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not 1 <= len(v) <= 200:
            raise ValueError("full_name must be 1-200 characters")
        return v

    @field_validator("job_title")
    @classmethod
    def _title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 100:
            raise ValueError("job_title must be at most 100 characters")
        return v or None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        if v not in available_timezones():
            raise ValueError("Unknown timezone")
        return v


class PasswordChange(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return validate_password_strength(v)


class TokenOnly(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRoleUpdate(BaseModel):
    role: UserRole


class User(BaseModel):
    """User as seen in an admin tenant listing. `role` is the user's role in the
    active tenant (populated by the endpoint)."""
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool = True
    is_super_admin: bool = False
    role: Optional[str] = None

    class Config:
        from_attributes = True


class UserMe(BaseModel):
    """The authenticated user plus their active-tenant context and memberships."""
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool = True
    is_super_admin: bool = False
    # `role` mirrors the active-tenant role (or 'super_admin') so existing
    # frontend role checks keep working.
    role: Optional[str] = None
    active_tenant_id: Optional[int] = None
    memberships: List[MembershipOut] = []
    job_title: Optional[str] = None
    timezone: Optional[str] = None
    has_avatar: bool = False
    mfa_enabled: bool = False
    has_password: bool = True
