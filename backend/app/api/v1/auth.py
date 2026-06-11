from datetime import timedelta
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core import security
from app.core.config import settings
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.user import Token

router = APIRouter()


async def _default_active_tenant_id(db: AsyncSession, user: User) -> Optional[int]:
    """Pick the tenant a freshly-issued token should start in."""
    if user.is_super_admin:
        res = await db.execute(select(Tenant.id).order_by(Tenant.id).limit(1))
        return res.scalars().first()
    res = await db.execute(
        select(TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user.id)
        .order_by(TenantMembership.tenant_id)
        .limit(1)
    )
    return res.scalars().first()


@router.post("/login/access-token", response_model=Token)
async def login_access_token(
    db: AsyncSession = Depends(deps.get_db), form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """OAuth2 compatible token login, get an access token for future requests."""
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalars().first()

    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    active_tenant_id = await _default_active_tenant_id(db, user)
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return {
        "access_token": security.create_access_token(
            {"sub": user.email, "active_tenant_id": active_tenant_id},
            expires_delta=access_token_expires,
        ),
        "token_type": "bearer",
    }


class SwitchTenantRequest(BaseModel):
    tenant_id: int


@router.post("/switch-tenant", response_model=Token)
async def switch_tenant(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: SwitchTenantRequest,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    """Re-issue a token whose active tenant is the requested one.

    Regular users may only switch into tenants they are a member of. Super
    admins may switch into any existing tenant.
    """
    if current_user.is_super_admin:
        res = await db.execute(select(Tenant).where(Tenant.id == body.tenant_id))
        if not res.scalars().first():
            raise HTTPException(status_code=404, detail="Tenant not found")
    else:
        res = await db.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == current_user.id,
                TenantMembership.tenant_id == body.tenant_id,
            )
        )
        if not res.scalars().first():
            raise HTTPException(status_code=403, detail="You are not a member of that tenant.")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return {
        "access_token": security.create_access_token(
            {"sub": current_user.email, "active_tenant_id": body.tenant_id},
            expires_delta=access_token_expires,
        ),
        "token_type": "bearer",
    }
