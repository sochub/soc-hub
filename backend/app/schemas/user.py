from typing import List, Optional
from pydantic import BaseModel, EmailStr, field_validator
from app.core.passwords import validate_password_strength
from app.models.user import UserRole
from app.schemas.membership import MembershipOut


class Token(BaseModel):
    access_token: str
    token_type: str


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

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return validate_password_strength(v)


class UserUpdate(BaseModel):
    """Self-service profile update (own name / password)."""
    full_name: Optional[str] = None
    password: Optional[str] = None

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return validate_password_strength(v)


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
