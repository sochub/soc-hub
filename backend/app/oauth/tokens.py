"""OAuth access tokens: issuing them and verifying them (the one check a protected resource, e.g. the MCP
server, calls on every request)."""
import logging
import time
from dataclasses import dataclass
from typing import Optional

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.login_policy import member_may_enter
from app.core.config import settings
from app.models.auth_session import AuthSession
from app.models.membership import TenantMembership
from app.models.oauth_client import OAuthClient
from app.models.tenant import Tenant
from app.models.user import User
from app.oauth.resources import RESOURCES, issuer, resource_by_url

logger = logging.getLogger(__name__)

ACCESS_TOKEN_SECONDS = 3600
PURPOSE = "oauth"


def issue_oauth_access_token(session: AuthSession, user: User) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer(), "aud": session.resource, "sub": user.email, "tid": session.tenant_id,
        "tv": int(user.token_version or 0), "sid": session.id, "cid": session.client_id,
        "scope": session.scope or "", "purpose": PURPOSE, "iat": now, "exp": now + ACCESS_TOKEN_SECONDS,
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def tenant_role(db: AsyncSession, user: User, *, tenant_id: Optional[int], resource_url: Optional[str],
                      amr: str, sso_tenant_id: Optional[int]) -> Optional[str]:
    """The live membership role if this session may act in the tenant for this resource, else None.

    Requires: active user, active tenant, the resource's tenant gate (e.g. MCP enabled), a real membership
    (super admins included), and the tenant's member login method satisfied by the session's amr."""
    if not user.is_active or tenant_id is None:
        return None
    resource = resource_by_url(resource_url) if resource_url else None
    if resource is None:
        return None
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id)
                               .execution_options(populate_existing=True))).scalar_one_or_none()
    if tenant is None or tenant.is_active is False or not resource.tenant_gate(tenant):
        return None
    role = (await db.execute(select(TenantMembership.role).where(
        TenantMembership.user_id == user.id, TenantMembership.tenant_id == tenant_id))).scalar_one_or_none()
    if role is None:
        return None
    if not member_may_enter(tenant.member_login_method, amr=amr, sso_tenant_id=sso_tenant_id, tenant_id=tenant_id,
                            role=role, mfa_enabled=user.mfa_enabled_at is not None,
                            is_super_admin=bool(user.is_super_admin)):
        return None
    return role


@dataclass
class OAuthPrincipal:
    user: User
    tenant_id: int
    role: str
    session_id: str
    client_id: str
    client_name: str
    scopes: tuple[str, ...]


async def verify_oauth_access_token(db: AsyncSession, token: str, *, resource: str) -> Optional[OAuthPrincipal]:
    """Validate an OAuth access token for ``resource`` (a RESOURCES key). Returns None on any failure, never
    raises, never commits. Everything but the signature is re-checked live: user, tv, session, membership,
    tenant gate and login policy; the role comes from the membership, never from the token."""
    try:
        return await _verify(db, token, resource)
    except Exception as exc:  # a verifier must fail closed, never 500
        logger.warning("oauth token verification error: %s", type(exc).__name__)
        return None


async def _verify(db: AsyncSession, token: str, resource_key: str) -> Optional[OAuthPrincipal]:
    res = RESOURCES.get(resource_key)
    if res is None or not isinstance(token, str) or not token:
        return None
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM], audience=res.url,
                            issuer=issuer(), options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError:
        return None
    sid, tid, cid, email = claims.get("sid"), claims.get("tid"), claims.get("cid"), claims.get("sub")
    if (claims.get("purpose") != PURPOSE or not isinstance(sid, str) or not isinstance(cid, str)
            or not isinstance(email, str) or not isinstance(tid, int) or isinstance(tid, bool)):
        return None
    user = (await db.execute(select(User).options(selectinload(User.memberships))
                             .where(User.email == email))).scalars().first()
    if user is None or not user.is_active or claims.get("tv") != int(user.token_version or 0):
        return None
    session = (await db.execute(select(AuthSession).where(
        AuthSession.id == sid, AuthSession.revoked_at.is_(None)))).scalar_one_or_none()
    if (session is None or session.kind != "oauth" or session.user_id != user.id or session.tenant_id != tid
            or session.client_id != cid or session.resource != res.url):
        return None
    role = await tenant_role(db, user, tenant_id=tid, resource_url=res.url, amr=session.amr,
                             sso_tenant_id=session.sso_tenant_id)
    if role is None:
        return None
    client = await db.get(OAuthClient, cid)
    if client is None:
        return None
    scopes = tuple(s for s in str(claims.get("scope") or "").split() if s in res.scopes)
    # Same per-request shape as a ``get_current_user`` user, so shared helpers work on either.
    user._active_tenant_id = tid
    user._amr = session.amr
    user._sso_tenant_id = session.sso_tenant_id
    user._session_id = sid
    return OAuthPrincipal(user=user, tenant_id=tid, role=role, session_id=sid, client_id=cid,
                          client_name=client.client_name, scopes=scopes)
