"""Sessions & connected apps: a user's own live sessions, and a tenant admin's view of its OAuth apps.

Only live sessions are listed (not revoked, used within REFRESH_IDLE_DAYS). Refresh hashes are never
returned. Every revoke passes a scope (user_id for self, tenant_id for admin)."""
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.audit.events import audit_event
from app.auth import sessions
from app.core.config import settings
from app.models.auth_session import AuthSession
from app.models.oauth_client import OAuthClient
from app.models.tenant import Tenant
from app.models.user import User

router = APIRouter()          # mounted under /users (before users.router)
tenant_router = APIRouter()   # mounted under /tenants (before tenants.router)


class SessionOut(BaseModel):
    id: str
    kind: str
    current: bool
    client_name: Optional[str] = None
    tenant_id: Optional[int] = None
    tenant_name: Optional[str] = None
    amr: str
    created_at: datetime
    last_used_at: datetime
    user_agent: Optional[str] = None
    ip: Optional[str] = None


class TenantOAuthSessionOut(BaseModel):
    id: str
    client_name: Optional[str] = None
    user_email: str
    amr: str
    created_at: datetime
    last_used_at: datetime
    ip: Optional[str] = None


def _live_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=settings.REFRESH_IDLE_DAYS)


@router.get("/me/sessions", response_model=List[SessionOut])
async def list_my_sessions(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    rows = (await db.execute(
        select(AuthSession, OAuthClient.client_name, Tenant.name)
        .outerjoin(OAuthClient, OAuthClient.id == AuthSession.client_id)
        .outerjoin(Tenant, Tenant.id == AuthSession.tenant_id)
        .where(AuthSession.user_id == current_user.id, AuthSession.revoked_at.is_(None),
               AuthSession.last_used_at >= _live_cutoff())
        .order_by(AuthSession.last_used_at.desc())
    )).all()
    cur = getattr(current_user, "_session_id", None)
    return [SessionOut(id=s.id, kind=s.kind, current=(s.id == cur), client_name=cn, tenant_id=s.tenant_id,
                       tenant_name=tn, amr=s.amr, created_at=s.created_at, last_used_at=s.last_used_at,
                       user_agent=s.user_agent, ip=s.ip) for s, cn, tn in rows]


@router.delete("/me/sessions/{session_id}", status_code=204)
async def revoke_my_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Response:
    if not await sessions.revoke(db, session_id, user_id=current_user.id):
        raise HTTPException(status_code=404, detail="Not found")
    await db.commit()
    await audit_event("security", "session.revoked", request=request, user_id=current_user.id,
                      user_email=current_user.email, entity_type="session")
    return Response(status_code=204)


@tenant_router.get("/current/oauth-sessions", response_model=List[TenantOAuthSessionOut])
async def list_tenant_oauth_sessions(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    rows = (await db.execute(
        select(AuthSession, OAuthClient.client_name, User.email)
        .join(User, User.id == AuthSession.user_id)
        .outerjoin(OAuthClient, OAuthClient.id == AuthSession.client_id)
        .where(AuthSession.tenant_id == tenant_id, AuthSession.kind == "oauth",
               AuthSession.revoked_at.is_(None), AuthSession.last_used_at >= _live_cutoff())
        .order_by(AuthSession.last_used_at.desc())
    )).all()
    return [TenantOAuthSessionOut(id=s.id, client_name=cn, user_email=email, amr=s.amr,
                                  created_at=s.created_at, last_used_at=s.last_used_at, ip=s.ip)
            for s, cn, email in rows]


@tenant_router.delete("/current/oauth-sessions/{session_id}", status_code=204)
async def revoke_tenant_oauth_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Response:
    if not await sessions.revoke(db, session_id, tenant_id=tenant_id):
        raise HTTPException(status_code=404, detail="Not found")
    await db.commit()
    await audit_event("security", "session.revoked_by_admin", request=request, user_id=current_user.id,
                      user_email=current_user.email, tenant_id=tenant_id, entity_type="session")
    return Response(status_code=204)
