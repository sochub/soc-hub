import logging
import secrets
from datetime import timedelta
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core import security
from app.core.config import settings
from app.core.login_throttle import LoginThrottle, client_ip, get_login_throttle
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.user import Token

router = APIRouter()
logger = logging.getLogger(__name__)

# Verified against when the email is unknown, so a miss costs the same Argon2
# work as a wrong password and response timing doesn't reveal which emails exist.
_DUMMY_HASH = security.get_password_hash(secrets.token_urlsafe(32))


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
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
    throttle: LoginThrottle = Depends(get_login_throttle),
) -> Any:
    """OAuth2 compatible token login, get an access token for future requests."""
    email = form_data.username.strip().lower()
    ip = client_ip(request)

    retry_after = await throttle.retry_after(email, ip)
    if retry_after:
        logger.warning("login throttled email=%r ip=%s retry_after=%ss", email[:64], ip, retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
            headers={"Retry-After": str(retry_after)},
        )

    result = await db.execute(select(User).where(func.lower(User.email) == email))
    user = result.scalars().first()

    # Always run one Argon2 verify, even for unknown emails (constant-time miss).
    password_ok = security.verify_password(
        form_data.password, user.hashed_password if user else _DUMMY_HASH
    )
    reason = None
    if not user:
        reason = "unknown email"
    elif not password_ok:
        reason = "bad password"
    elif not user.is_active:
        reason = "inactive user"
    if reason:
        await throttle.record_failure(email, ip)
        logger.warning("failed login email=%r ip=%s reason=%s", email[:64], ip, reason)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await throttle.reset(email)
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
