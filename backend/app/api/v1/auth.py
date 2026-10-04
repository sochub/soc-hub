import logging
import secrets
import jwt
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
from app.api.v1.mfa import get_mfa_throttle, get_redis, verify_user_code
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


_default_active_tenant_id = deps.default_active_tenant_id


@router.post("/login/access-token", response_model=Token, response_model_exclude_unset=True)
async def login_access_token(
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
    throttle: LoginThrottle = Depends(get_login_throttle),
) -> Any:
    """OAuth2 compatible token login, get an access token for future requests."""
    email = form_data.username.strip().lower()
    ip = client_ip(request)

    # Reserve the attempt atomically BEFORE checking the password, so parallel
    # guesses can't all pass a check-then-act race.
    retry_after = await throttle.reserve(email, ip)
    if retry_after:
        logger.warning("login throttled email=%r ip=%s retry_after=%ss", email[:64], ip, retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
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
        logger.warning("failed login email=%r ip=%s reason=%s", email[:64], ip, reason)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await throttle.reset(email, ip)
    if user.mfa_enabled_at is not None:
        return {"mfa_required": True, "mfa_token": security.issue_mfa_challenge(user)}
    if await _tenant_requires_mfa(db, user):
        return {"mfa_setup_required": True, "mfa_token": security.issue_mfa_challenge(user)}
    active_tenant_id = await _default_active_tenant_id(db, user)
    return {
        "access_token": security.issue_access_token(user, active_tenant_id),
        "token_type": "bearer",
    }


async def _tenant_requires_mfa(db: AsyncSession, user: User, tenant_id: Optional[int] = None) -> bool:
    """True if the user (without MFA) belongs to / targets a tenant that requires MFA.

    Super admins with no memberships are exempt. `tenant_id` limits the check to that tenant.
    """
    q = (select(Tenant.id).join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
         .where(TenantMembership.user_id == user.id, Tenant.require_mfa.is_(True)))
    if tenant_id is not None:
        q = q.where(Tenant.id == tenant_id)
    return (await db.execute(q.limit(1))).first() is not None


class MfaLoginBody(BaseModel):
    mfa_token: str
    code: str


@router.post("/login/mfa", response_model=Token, response_model_exclude_unset=True)
async def login_mfa(
    request: Request,
    body: MfaLoginBody,
    db: AsyncSession = Depends(deps.get_db),
    redis=Depends(get_redis),
    throttle=Depends(get_mfa_throttle),
) -> Any:
    """Second login step: challenge token + TOTP code -> access token."""
    expired = HTTPException(status_code=401, detail="Login expired — sign in again",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = security.decode_mfa_challenge(body.mfa_token)
    except jwt.PyJWTError:
        raise expired
    email = payload.get("sub")
    user = None
    if isinstance(email, str):
        user = (await db.execute(select(User).where(func.lower(User.email) == email.lower()))).scalars().first()
    if (not user or not user.is_active or payload.get("tv") != (user.token_version or 0)
            or user.mfa_enabled_at is None):
        raise expired
    await verify_user_code(db, redis, throttle, user, body.code, client_ip(request))
    return {
        "access_token": security.issue_access_token(user, await _default_active_tenant_id(db, user)),
        "token_type": "bearer",
    }


class SwitchTenantRequest(BaseModel):
    tenant_id: int


@router.post("/switch-tenant", response_model=Token, response_model_exclude_unset=True)
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

    if (not current_user.is_super_admin and current_user.mfa_enabled_at is None
            and await _tenant_requires_mfa(db, current_user, body.tenant_id)):
        raise HTTPException(status_code=403, detail="mfa_setup_required")

    return {
        "access_token": security.issue_access_token(current_user, body.tenant_id),
        "token_type": "bearer",
    }
