import asyncio
from typing import Optional
from fastapi import Depends, Request, HTTPException, Query, Security, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
import jwt
from jwt import PyJWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.audit.context import ctx_of
from app.auth import sessions
from app.auth.login_policy import AMR_PWD
from app.auth.login_tenant import method_refusal, session_may_enter, tenant_deactivated_error
from app.core import session_cookie
from app.core.config import settings
from app.db.session import get_db
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.models.webhook import PREFIX_LEN, Webhook, verify_webhook_key
from app.schemas import user as user_schema
from app.utils.roles import resolve_active_role

# auto_error=False: the browser session arrives as a cookie instead (app.core.session_cookie).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login/access-token", auto_error=False)


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db), token: Optional[str] = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = session_cookie.request_token(request, token)
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        sid = payload.get("sid")
        # Every member token belongs to a server-side session: tokens without one (minted before
        # sessions existed) are refused, so everyone signs in once after the upgrade.
        if email is None or payload.get("purpose") or not isinstance(sid, str) or not sid:
            raise credentials_exception
        token_data = user_schema.TokenData(
            email=email, active_tenant_id=payload.get("active_tenant_id")
        )
    except PyJWTError:
        raise credentials_exception

    result = await db.execute(
        select(User).options(selectinload(User.memberships)).where(User.email == token_data.email)
    )
    user = result.scalars().first()
    if user is None or payload.get("tv", 0) != (user.token_version or 0):
        raise credentials_exception
    # Revocation (logout, credential change, admin) is checked on every request.
    session = await sessions.is_live(db, sid)
    if session is None or session.kind != "web" or session.user_id != user.id:
        raise credentials_exception
    # is_live may have bumped last_used_at (row lock): commit now so no other request or refresh of this
    # session waits on this request's transaction.
    await db.commit()
    user._session_id = sid
    # Transient, request-scoped: the active tenant chosen for this token.
    user._active_tenant_id = token_data.active_tenant_id
    ctx = ctx_of(request)
    if ctx is not None:
        ctx.user_id, ctx.user_email = user.id, user.email
        # Provisional, and only for a real membership: platform-level routes and stale-membership denials
        # stay tenant-less (NULL) so tenant admins never see them. get_effective_tenant_id refines it.
        if any(m.tenant_id == token_data.active_tenant_id for m in user.memberships):
            ctx.tenant_id = token_data.active_tenant_id
    # How this session authenticated. Tokens minted before amr existed count as password sessions.
    user._amr = payload.get("amr") or AMR_PWD
    user._sso_tenant_id = payload.get("sso_tid")
    active = token_data.active_tenant_id
    # A deactivated active tenant: get_effective_tenant_id refuses it and no role resolves there (for members;
    # the super admin is break-glass and keeps access to inspect and reactivate it).
    user._tenant_deactivated = False
    if active is not None:  # re-checked every request, so a login-method change cuts live sessions
        row = (await db.execute(
            select(Tenant.member_login_method, Tenant.slug, Tenant.is_active).where(Tenant.id == active))).first()
        if row is not None:
            method, slug, tenant_active = row
            if not session_may_enter(user, active, method):
                raise method_refusal(user, active, method, slug)
            user._tenant_deactivated = tenant_active is False and not user.is_super_admin
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def _active_role(current_user: User) -> Optional[str]:
    """Resolve the caller's role string in their active tenant ('super_admin',
    'admin', 'analyst', 'viewer', or None)."""
    if getattr(current_user, "_tenant_deactivated", False):
        return None
    active = getattr(current_user, "_active_tenant_id", None)
    return resolve_active_role(current_user.is_super_admin, active, current_user.memberships)


def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    if _active_role(current_user) in ("super_admin", "admin"):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
    )


def require_analyst_or_above(current_user: User = Depends(get_current_active_user)) -> User:
    if _active_role(current_user) in ("super_admin", "admin", "analyst"):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
    )


def require_super_admin(request: Request, current_user: User = Depends(get_current_active_user)) -> User:
    """Strict super-admin-only check. Marks the request platform-level for the audit log."""
    ctx = ctx_of(request)
    if ctx is not None:
        ctx.platform = True
    if not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required"
        )
    return current_user


async def tenant_requires_mfa(db: AsyncSession, user: User, tenant_id: Optional[int] = None) -> bool:
    """Does a tenant's "require two-factor" setting apply to `user`?

    With `tenant_id`: whether that tenant requires MFA (checked directly — the
    caller has already established access). Without: whether any tenant the user
    can reach requires it — their memberships, or, for super admins (who can
    enter every tenant), any tenant at all.
    """
    q = select(Tenant.id).where(Tenant.require_mfa.is_(True))
    if tenant_id is not None:
        q = q.where(Tenant.id == tenant_id)
    elif not user.is_super_admin:
        q = q.join(TenantMembership, TenantMembership.tenant_id == Tenant.id).where(
            TenantMembership.user_id == user.id)
    return (await db.execute(q.limit(1))).first() is not None


def _set_tenant(request: Request, tenant_id: int) -> None:
    ctx = ctx_of(request)
    if ctx is not None:
        ctx.tenant_id, ctx.tenant_explicit = tenant_id, True


async def get_effective_tenant_id(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    tenant_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> int:
    """Resolve the tenant to operate in.

    Super admins use the active tenant from the token, with an optional
    ``?tenant_id=`` override (refused with 403 ``mfa_setup_required`` when the
    super admin has no MFA and the target tenant requires it). Regular users use
    the active tenant from the token, which must be one of their memberships.
    """
    active = getattr(current_user, "_active_tenant_id", None)

    if current_user.is_super_admin:
        chosen = tenant_id if tenant_id is not None else active
        if chosen is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Super admin must select a tenant.",
            )
        if tenant_id is not None and tenant_id != active:
            from app.models.tenant import Tenant
            if (await db.execute(select(Tenant.id).where(Tenant.id == tenant_id))).first() is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        if (tenant_id is not None and tenant_id != active and current_user.mfa_enabled_at is None
                and await tenant_requires_mfa(db, current_user, tenant_id)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mfa_setup_required")
        _set_tenant(request, chosen)
        return chosen

    if active is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active tenant selected.",
        )
    if not any(m.tenant_id == active for m in current_user.memberships):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of the active tenant.",
        )
    if getattr(current_user, "_tenant_deactivated", False):
        raise tenant_deactivated_error()
    _set_tenant(request, active)
    return active


# Named so the OpenAPI reference shows the webhook auth as an API key in the
# X-API-Key header. auto_error=False keeps our own 401 below (a missing header
# returns 401, matching the invalid-key behavior verify_webhooks.py asserts).
# Argon2 verify costs tens of ms; alert floods reuse the same few keys, so remember recent successes.
# An entry only counts while the row still has the same id and hash (rotation/deletion invalidates it).
# ponytail: per-process FIFO, plaintext keys in memory only; a shared cache isn't needed at this scale.
_VERIFIED_CACHE_SIZE = 256
_verified_webhooks: dict = {}

webhook_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="WebhookApiKey")


async def get_webhook_from_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Security(webhook_api_key_scheme),
) -> Webhook:
    """Resolve the webhook that owns the supplied API key.

    Each webhook belongs to one tenant; the key alone determines both the
    destination tenant and the alert's source name. The owning tenant must be
    active.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    candidates = (await db.execute(
        select(Webhook)
        .join(Tenant, Tenant.id == Webhook.tenant_id)
        .where(
            Webhook.key_prefix == x_api_key[:PREFIX_LEN],  # pre-Argon2 rows (8-char prefix) never match
            Tenant.is_active == True,  # noqa: E712
        )
    )).scalars().all()
    webhook = None
    for row in candidates:
        if _verified_webhooks.get(x_api_key) == (row.id, row.key_hash):
            webhook = row
            break
    if webhook is None:
        for row in candidates:
            if await asyncio.to_thread(verify_webhook_key, x_api_key, row.key_hash):
                webhook = row
                break
        if webhook is not None:
            if len(_verified_webhooks) >= _VERIFIED_CACHE_SIZE:
                _verified_webhooks.pop(next(iter(_verified_webhooks)))
            _verified_webhooks[x_api_key] = (webhook.id, webhook.key_hash)
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    ctx = ctx_of(request)
    if ctx is not None:
        ctx.tenant_id, ctx.actor = webhook.tenant_id, f"webhook:{webhook.id}"
    return webhook


async def default_active_tenant_id(db: AsyncSession, user: User) -> Optional[int]:
    """Pick the tenant a freshly-issued token should start in."""
    if user.is_super_admin:
        res = await db.execute(select(Tenant.id).order_by(Tenant.id).limit(1))
        return res.scalars().first()
    res = await db.execute(
        select(TenantMembership.tenant_id)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == user.id)
        .order_by(Tenant.is_active.is_(False), TenantMembership.tenant_id)
        .limit(1)
    )
    return res.scalars().first()


async def get_user_for_mfa_setup(
    request: Request,
    db: AsyncSession = Depends(get_db), token: Optional[str] = Depends(oauth2_scheme)
) -> tuple[User, bool]:
    """Caller for MFA setup/enable: a normal access token, OR an MFA challenge
    token for a user who has not enabled MFA yet (forced enrolment at login).
    Returns (user, via_challenge)."""
    from app.core.security import decode_mfa_challenge

    bad = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    header_token, token = token, session_cookie.request_token(request, token)
    if not token:
        raise bad
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except PyJWTError:
        raise bad
    if not payload.get("purpose"):
        return get_current_active_user(await get_current_user(request=request, db=db, token=header_token)), False
    try:
        payload = decode_mfa_challenge(token)
    except PyJWTError:
        raise bad
    email = payload.get("sub")
    user = (await db.execute(
        select(User).options(selectinload(User.memberships)).where(User.email == email)
    )).scalars().first() if email else None
    if (user is None or not user.is_active or payload.get("tv", 0) != (user.token_version or 0)
            or user.mfa_enabled_at is not None):
        raise bad
    user._active_tenant_id = None
    user._amr, user._sso_tenant_id = AMR_PWD, None  # a challenge only follows a password sign-in
    user._mfa_challenge_jti = payload.get("jti")
    return user, True
