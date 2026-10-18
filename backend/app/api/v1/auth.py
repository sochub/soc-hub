import logging
import re
import secrets
import jwt
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.audit.events import audit_event
from app.audit.redact import email_domain
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


_DOMAIN_RE = re.compile(r"^[a-z0-9.-]{1,253}$")


def _safe_domain(email: str) -> Optional[str]:
    d = email_domain(email)
    return d if d and _DOMAIN_RE.match(d) else None


async def _login_tenant(db: AsyncSession, email: str) -> Optional[int]:
    """Attribute a failed login to a tenant only when the email has exactly one membership (spec 2.5).
    One query keyed on the email whether or not the user exists, so timing doesn't reveal it."""
    rows = (await db.execute(
        select(TenantMembership.tenant_id).join(User, User.id == TenantMembership.user_id)
        .where(func.lower(User.email) == email))).scalars().all()
    return rows[0] if len(rows) == 1 else None

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
        await audit_event("security", "login.failed", request=request, outcome="denied",
                          changes={"email_domain": _safe_domain(email)})
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
        await audit_event("security", "login.locked", request=request, outcome="denied",
                          tenant_id=await _login_tenant(db, email), changes={"email_domain": _safe_domain(email)})
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
        await audit_event("security", "login.failed", request=request, outcome="denied",
                          tenant_id=await _login_tenant(db, email), changes={"email_domain": _safe_domain(email)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await throttle.reset(email, ip)
    if user.mfa_enabled_at is not None:
        await audit_event("security", "login.mfa_required", request=request, user_id=user.id,
                          user_email=user.email, tenant_id=await _default_active_tenant_id(db, user))
        return {"mfa_required": True, "mfa_token": security.issue_mfa_challenge(user)}
    if await deps.tenant_requires_mfa(db, user):
        await audit_event("security", "login.mfa_required", request=request, user_id=user.id,
                          user_email=user.email, tenant_id=await _default_active_tenant_id(db, user))
        return {"mfa_setup_required": True, "mfa_token": security.issue_mfa_challenge(user)}
    active_tenant_id = await _default_active_tenant_id(db, user)
    await audit_event("security", "login.success", request=request, user_id=user.id, user_email=user.email,
                      tenant_id=active_tenant_id)
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
    try:
        await verify_user_code(db, redis, throttle, user, body.code, client_ip(request))
    except HTTPException:
        await audit_event("security", "mfa.challenge_failed", request=request, outcome="denied",
                          user_id=user.id, user_email=user.email, tenant_id=await _default_active_tenant_id(db, user))
        raise
    if not await spend_mfa_challenge(redis, payload.get("jti")):
        raise expired  # this challenge was already used to sign in
    tid = await _default_active_tenant_id(db, user)
    await audit_event("security", "mfa.challenge_passed", request=request, user_id=user.id,
                      user_email=user.email, tenant_id=tid)
    await audit_event("security", "login.success", request=request, user_id=user.id, user_email=user.email,
                      tenant_id=tid)
    return {
        "access_token": security.issue_access_token(user, tid),
        "token_type": "bearer",
    }


class SwitchTenantRequest(BaseModel):
    tenant_id: int


@router.post("/switch-tenant", response_model=Token, response_model_exclude_unset=True)
async def switch_tenant(
    *,
    request: Request,
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

    await audit_event("security", "tenant.switch", request=request, user_id=current_user.id,
                      user_email=current_user.email, tenant_id=body.tenant_id,
                      changes={"from_tenant_id": getattr(current_user, "_active_tenant_id", None)})
    return {
        "access_token": security.issue_access_token(current_user, body.tenant_id),
        "token_type": "bearer",
    }


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Response:
    """Record the sign-out. JWTs are stateless: the client discards the token."""
    await audit_event("security", "logout", request=request, user_id=current_user.id,
                      user_email=current_user.email, tenant_id=tenant_id)
    return Response(status_code=204)
