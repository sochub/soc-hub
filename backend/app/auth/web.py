"""Web (browser and API-client) sign-in sessions: every member access token belongs to a live
``auth_sessions`` row (``sid`` claim) and comes with a rotating refresh token.

- ``start_web_session``: a new sign-in (password, password+MFA, SAML, OIDC, forced MFA enrolment).
- ``reissue_for_session``: a credential change on the current session (password, MFA on/off): the session
  takes the new token version and a new refresh secret; every other session of the user is revoked.
- ``switch_session_tenant``: same session, new active tenant.
- ``refresh_tenant``: the active tenant a refreshed access token may carry (login policy re-applied).
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import sessions
from app.auth.login_tenant import session_may_enter
from app.core import security, session_cookie
from app.models.auth_session import AuthSession
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User

KIND = "web"


def _gone() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials",
                         headers={"WWW-Authenticate": "Bearer"})


async def start_web_session(db: AsyncSession, request: Request, response: Response, user: User,
                            tenant_id: Optional[int], *, amr: str, sso_tenant_id: Optional[int] = None) -> dict:
    """Create a web session, commit, set the access + refresh cookies; returns the token response body
    (tokens in it only for API clients)."""
    row, refresh_token = await sessions.create_session(db, user, kind=KIND, amr=amr, sso_tenant_id=sso_tenant_id,
                                                       tenant_id=tenant_id, request=request)
    await db.commit()
    token = security.issue_access_token(user, tenant_id, sid=row.id, amr=amr, sso_tenant_id=sso_tenant_id)
    return session_cookie.issue(request, response, token, refresh_token)


async def reissue_for_session(db: AsyncSession, request: Request, response: Response, user: User, *,
                              sid: str) -> dict:
    """After ``bump_token_version(user)`` (committed by the caller): carry the current session over to the
    new token version with a fresh refresh secret, and revoke the user's other live sessions (web and
    OAuth). Sets both cookies; returns the token response body."""
    row = (await db.execute(select(AuthSession).where(AuthSession.id == sid).with_for_update()
                            .execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.user_id != user.id or row.kind != KIND:
        raise _gone()
    row.tv = int(user.token_version or 0)
    row.last_used_at = datetime.now(timezone.utc)
    refresh_token = sessions.rekey(row)
    await db.execute(update(AuthSession).where(
        AuthSession.user_id == user.id, AuthSession.id != sid, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc)).execution_options(synchronize_session=False))
    await db.commit()
    token = security.issue_access_token(user, row.tenant_id, sid=row.id, amr=row.amr,
                                        sso_tenant_id=row.sso_tenant_id)
    return session_cookie.issue(request, response, token, refresh_token)


async def switch_session_tenant(db: AsyncSession, request: Request, response: Response, user: User,
                                tenant_id: int) -> dict:
    """Same session, new active tenant: re-issue the access token only (the refresh token is unchanged)."""
    sid = user._session_id
    await db.execute(update(AuthSession).where(AuthSession.id == sid, AuthSession.user_id == user.id)
                     .values(tenant_id=tenant_id).execution_options(synchronize_session=False))
    await db.commit()
    token = security.issue_access_token(user, tenant_id, sid=sid, amr=user._amr, sso_tenant_id=user._sso_tenant_id)
    return session_cookie.issue(request, response, token)


async def _may_act_in(db: AsyncSession, user: User, tenant_id: int) -> bool:
    """Membership (any existing tenant for super admins) and the login policy for this session."""
    method = (await db.execute(select(Tenant.member_login_method).where(Tenant.id == tenant_id))).scalar()
    if method is None:
        return False
    if not user.is_super_admin and not any(m.tenant_id == tenant_id for m in user.memberships):
        return False
    return session_may_enter(user, tenant_id, method)


async def refresh_tenant(db: AsyncSession, user: User, session: AuthSession) -> tuple[bool, Optional[int]]:
    """The active tenant a refreshed token carries: the session's own if this session may still act there,
    else the lowest-id tenant it may enter. ``user`` must carry ``_amr``/``_sso_tenant_id`` and loaded
    memberships. Returns ``(ok, tenant_id)``; ``(True, None)`` for a user with no tenant at all,
    ``(False, None)`` when tenants exist but the policy admits none."""
    if session.tenant_id is not None and await _may_act_in(db, user, session.tenant_id):
        return True, session.tenant_id
    if user.is_super_admin:
        rows = (await db.execute(select(Tenant.id, Tenant.member_login_method).order_by(Tenant.id))).all()
    else:
        rows = (await db.execute(
            select(Tenant.id, Tenant.member_login_method)
            .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
            .where(TenantMembership.user_id == user.id).order_by(Tenant.id))).all()
    for tid, method in rows:
        if session_may_enter(user, tid, method):
            return True, tid
    return (not rows), None
