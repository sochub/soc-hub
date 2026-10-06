"""MFA (TOTP) management: enrol, enable, disable, admin reset, tenant requirement.

Never log codes, secrets, otpauth URIs or tokens. Only /me/mfa/setup returns the secret.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.api.v1.users import _audit_tenant
from app.core import security, totp
from app.core.login_throttle import LoginThrottle, client_ip, get_login_throttle, get_pwd_throttle
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.user import Token
from app.utils.audit import create_audit_log
from app.utils.crypto import decrypt, encrypt

router = APIRouter()           # mounted under /users
security_router = APIRouter()  # mounted under /tenants
logger = logging.getLogger(__name__)

INVALID_CODE = "Invalid or expired code"
LOGIN_EXPIRED = "Login expired — sign in again"


async def spend_mfa_challenge(redis, jti) -> bool:
    """Mark an MFA challenge as used (single use). False if missing or already spent."""
    if not isinstance(jti, str) or not jti:
        return False
    return bool(await redis.set(f"mfa:chal:{jti}", 1, nx=True,
                                ex=security.MFA_CHALLENGE_MINUTES * 60))


# 5 failures per window, per user; keys live under mfa:attempts:... (never login:).
_mfa_throttle = get_login_throttle().namespaced("mfa", max_email_ip=5, max_email=5)


def get_mfa_throttle() -> LoginThrottle:
    """Dedicated throttle for MFA code attempts; tests override it."""
    return _mfa_throttle


async def _lock_user(db: AsyncSession, user_id: int) -> User:
    """Re-read the user row under FOR UPDATE so MFA state changes serialize."""
    return (await db.execute(
        select(User).options(selectinload(User.memberships)).where(User.id == user_id)
        .with_for_update(of=User)
        .execution_options(populate_existing=True)
    )).scalars().one()


def get_redis():
    """Redis client (same lazy factory as the login throttle); tests override this."""
    return get_login_throttle().redis


async def verify_user_code(db: AsyncSession, redis, throttle: LoginThrottle, user: User,
                           code: str, ip: str) -> None:
    """Check a TOTP code for `user` (throttled, replay-protected). Raises HTTPException."""
    key = str(user.id)  # the throttle namespaces it (mfa:attempts:...)
    retry_after = await throttle.reserve(key, ip)
    if retry_after:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})
    try:
        secret = decrypt(user.mfa_secret_enc or "")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("mfa secret undecryptable user_id=%s", user.id)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- logs user id only
        raise HTTPException(status_code=409, detail="MFA unavailable — contact your admin")
    step = totp.matching_step(secret, code)
    if step is None or not await totp.consume_step(redis, user.id, step):
        raise HTTPException(status_code=400, detail=INVALID_CODE)
    await throttle.reset(key, ip)


class EnableBody(BaseModel):
    code: str = Field(max_length=16)
    # Required when enabling from a normal session on an account with a password
    # (the forced-setup challenge path already verified the password).
    current_password: Optional[str] = Field(default=None, max_length=1024)


class DisableBody(BaseModel):
    current_password: str = Field(max_length=1024)
    code: str = Field(max_length=16)


async def _check_current_password(pwd_throttle: LoginThrottle, user: User, password: Optional[str],
                                  ip: str) -> None:
    """Throttled current-password check (keys pwd:attempts:...). Raises HTTPException."""
    if not password:
        raise HTTPException(status_code=400, detail="Enter your current password")
    key = str(user.id)
    retry_after = await pwd_throttle.reserve(key, ip)
    if retry_after:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})
    if not security.verify_password(password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    await pwd_throttle.reset(key, ip)


class SetupOut(BaseModel):
    otpauth_uri: str
    secret: str


async def _audit(db, user: User, action: str, *, entity_type="user", entity_id=None, tenant_id=None,
                 actor_id=None):
    tid = tenant_id if tenant_id is not None else _audit_tenant(user)
    eid = entity_id if entity_id is not None else user.id
    if tid is None:  # audit_logs.tenant_id is NOT NULL
        logger.warning("mfa audit skipped user_id=%s action=%s", eid, action)
        return
    await create_audit_log(db=db, entity_type=entity_type, entity_id=eid, action=action,
                           tenant_id=tid, user_id=actor_id if actor_id is not None else user.id,
                           changes=None)


@router.post("/me/mfa/setup", response_model=SetupOut)
async def mfa_setup(
    db: AsyncSession = Depends(deps.get_db),
    caller: tuple = Depends(deps.get_user_for_mfa_setup),
) -> Any:
    user, _ = caller
    user = await _lock_user(db, user.id)
    if user.mfa_enabled_at is not None:
        raise HTTPException(status_code=409, detail="Two-factor authentication is already enabled")
    secret = totp.new_secret()
    user.mfa_secret_enc = encrypt(secret)
    await db.commit()
    return SetupOut(otpauth_uri=totp.provisioning_uri(secret, user.email), secret=secret)


@router.post("/me/mfa/enable", response_model=Token, response_model_exclude_unset=True)
async def mfa_enable(
    *,
    request: Request,
    body: EnableBody,
    db: AsyncSession = Depends(deps.get_db),
    caller: tuple = Depends(deps.get_user_for_mfa_setup),
    redis=Depends(get_redis),
    throttle: LoginThrottle = Depends(get_mfa_throttle),
    pwd_throttle: LoginThrottle = Depends(get_pwd_throttle),
) -> Any:
    user, via_challenge = caller
    jti = getattr(user, "_mfa_challenge_jti", None)
    ip = client_ip(request)
    if not via_challenge and user.password_login_enabled:
        await _check_current_password(pwd_throttle, user, body.current_password, ip)
    user = await _lock_user(db, user.id)  # verify against the secret as stored under the lock
    if user.mfa_enabled_at is not None:
        raise HTTPException(status_code=409, detail="Two-factor authentication is already enabled")
    if not user.mfa_secret_enc:
        raise HTTPException(status_code=400, detail="Start setup first")
    await verify_user_code(db, redis, throttle, user, body.code, ip)
    if via_challenge and not await spend_mfa_challenge(redis, jti):
        raise HTTPException(status_code=401, detail=LOGIN_EXPIRED, headers={"WWW-Authenticate": "Bearer"})
    active = (await deps.default_active_tenant_id(db, user) if via_challenge
              else getattr(user, "_active_tenant_id", None))
    user.mfa_enabled_at = datetime.now(timezone.utc)
    security.bump_token_version(user)
    await _audit(db, user, "mfa_enabled")
    await db.commit()
    return {"access_token": security.issue_access_token(user, active), "token_type": "bearer"}


@router.post("/me/mfa/disable", response_model=Token, response_model_exclude_unset=True)
async def mfa_disable(
    *,
    request: Request,
    body: DisableBody,
    db: AsyncSession = Depends(deps.get_db),
    user: User = Depends(deps.get_current_active_user),
    redis=Depends(get_redis),
    pwd_throttle: LoginThrottle = Depends(get_pwd_throttle),
    throttle: LoginThrottle = Depends(get_mfa_throttle),
) -> Any:
    if not user.password_login_enabled:
        raise HTTPException(status_code=400,
                            detail="Two-factor for SSO-only accounts can only be reset by an admin")
    user = await _lock_user(db, user.id)
    if user.mfa_enabled_at is None:
        raise HTTPException(status_code=400, detail="Two-factor authentication is not enabled")
    if await deps.tenant_requires_mfa(db, user):  # super admins: any tenant
        raise HTTPException(status_code=403, detail="Your organization requires two-factor authentication")
    ip = client_ip(request)
    await _check_current_password(pwd_throttle, user, body.current_password, ip)
    await verify_user_code(db, redis, throttle, user, body.code, ip)
    active = getattr(user, "_active_tenant_id", None)
    user.mfa_secret_enc = None
    user.mfa_enabled_at = None
    security.bump_token_version(user)
    await _audit(db, user, "mfa_disabled")
    await db.commit()
    return {"access_token": security.issue_access_token(user, active), "token_type": "bearer"}


@router.post("/{user_id}/mfa/reset", status_code=204)
async def mfa_reset(
    user_id: int,
    db: AsyncSession = Depends(deps.get_db),
    actor: User = Depends(deps.get_current_active_user),
) -> Response:
    """Super admin, or admin of the active tenant the target belongs to, clears the target's MFA."""
    role = deps._active_role(actor)
    active = getattr(actor, "_active_tenant_id", None)
    if role not in ("super_admin", "admin"):
        raise HTTPException(status_code=404, detail="Not found")
    if actor.id == user_id:
        raise HTTPException(status_code=400, detail="Use Turn off two-factor in your profile")
    target = (await db.execute(select(User).where(User.id == user_id))).scalars().first()
    if target is None or (target.is_super_admin and not actor.is_super_admin):
        raise HTTPException(status_code=404, detail="Not found")
    if actor.is_super_admin:
        audit_tid = (await db.execute(
            select(func.min(TenantMembership.tenant_id)).where(TenantMembership.user_id == user_id)
        )).scalar() or (await db.execute(select(func.min(Tenant.id)))).scalar()
    else:
        member = (await db.execute(select(TenantMembership.id).where(
            TenantMembership.user_id == user_id, TenantMembership.tenant_id == active))).first()
        if member is None:
            raise HTTPException(status_code=404, detail="Not found")
        audit_tid = active
    target = await _lock_user(db, user_id)
    target.mfa_secret_enc = None
    target.mfa_enabled_at = None
    security.bump_token_version(target)
    await _audit(db, actor, "mfa_reset", entity_id=target.id, tenant_id=audit_tid, actor_id=actor.id)
    await db.commit()
    return Response(status_code=204)


class SecurityOut(BaseModel):
    require_mfa: bool
    members_without_mfa: int


class SecurityIn(BaseModel):
    require_mfa: bool


async def _security_out(db: AsyncSession, tenant: Tenant) -> SecurityOut:
    n = (await db.execute(
        select(func.count()).select_from(TenantMembership)
        .join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant.id, User.is_active.is_(True),
               User.mfa_enabled_at.is_(None))
    )).scalar_one()
    return SecurityOut(require_mfa=bool(tenant.require_mfa), members_without_mfa=int(n))


@security_router.get("/current/security", response_model=SecurityOut)
async def get_tenant_security(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return await _security_out(db, tenant)


@security_router.put("/current/security", response_model=SecurityOut)
async def update_tenant_security(
    *,
    body: SecurityIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if bool(tenant.require_mfa) != body.require_mfa:
        tenant.require_mfa = body.require_mfa
        await create_audit_log(db=db, entity_type="tenant", entity_id=tenant.id,
                               action="require_mfa_changed", tenant_id=tenant.id,
                               user_id=current_user.id, changes={"require_mfa": body.require_mfa})
        await db.commit()
    return await _security_out(db, tenant)
