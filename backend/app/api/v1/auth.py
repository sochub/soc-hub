import logging
import re
import secrets
import jwt
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core import security
from app.api.v1.mfa import LOGIN_EXPIRED, get_mfa_throttle, get_redis, spend_mfa_challenge, verify_user_code
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

# Syntactic sanity check for the login username. Anything else is refused before
# a throttle slot is reserved, so arbitrary strings can't touch the counters.
_EMAIL_RE = re.compile(r"^[^@\s|]+@[^@\s|]+$")


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
    if len(email) > 320 or not _EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

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
    if await deps.tenant_requires_mfa(db, user):
        return {"mfa_setup_required": True, "mfa_token": security.issue_mfa_challenge(user)}
    active_tenant_id = await _default_active_tenant_id(db, user)
    return {
        "access_token": security.issue_access_token(user, active_tenant_id),
        "token_type": "bearer",
    }


class MfaLoginBody(BaseModel):
    mfa_token: str = Field(max_length=4096)
    code: str = Field(max_length=16)


@router.post("/login/mfa", response_model=Token, response_model_exclude_unset=True)
async def login_mfa(
    request: Request,
    body: MfaLoginBody,
    db: AsyncSession = Depends(deps.get_db),
    redis=Depends(get_redis),
    throttle=Depends(get_mfa_throttle),
) -> Any:
    """Second login step: challenge token + TOTP code -> access token."""
    expired = HTTPException(status_code=401, detail=LOGIN_EXPIRED,
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
    if not await spend_mfa_challenge(redis, payload.get("jti")):
        raise expired  # this challenge was already used to sign in
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
    admins may switch into any existing tenant. Without MFA, nobody (super admins
    included) may switch into a tenant that requires it.
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

    if (current_user.mfa_enabled_at is None
            and await deps.tenant_requires_mfa(db, current_user, body.tenant_id)):
        raise HTTPException(status_code=403, detail="mfa_setup_required")

    return {
        "access_token": security.issue_access_token(current_user, body.tenant_id),
        "token_type": "bearer",
    }
