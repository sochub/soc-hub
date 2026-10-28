"""Per-tenant ticketing connection (Jira, HaloPSA, ConnectWise) and case <-> ticket links.

The connection is admin-only and never returns credentials. Creating or syncing a case's ticket is an analyst
action. The periodic job in app/tasks/ticketing.py does the rest (auto-create, pull, push)."""
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Case
from app.models.ticket_connection import TICKET_PROVIDERS, TicketConnection
from app.models.user import User
from app.services import ticket_sync
from app.services.ticket_sync import PROVIDER_LABEL, SEVERITY_RANK, TicketError
from app.utils.audit import create_audit_log

router = APIRouter()

# What each provider needs; values are checked for presence only (the Test button checks they work).
REQUIRED = {
    "jira": ({"token"}, {"project_key"}),  # + email on Cloud
    "halopsa": ({"client_id", "client_secret"}, set()),
    "connectwise": ({"company_id", "public_key", "private_key", "client_id"}, {"board_id", "company_identifier"}),
}
SETTING_KEYS = {"project_key", "issue_type", "deployment", "tenant", "tickettype_id", "client_id",
                "closed_status_ids", "board_id", "company_identifier"}


class ConnectionIn(BaseModel):
    provider: str
    base_url: str = Field(max_length=500)
    credentials: Optional[Dict[str, str]] = None  # omitted = keep the stored ones (same provider and host only)
    settings: Dict[str, Any] = {}
    enabled: bool = True
    auto_create_min_severity: Optional[str] = None
    push_notes: bool = False

    @field_validator("provider")
    @classmethod
    def _provider(cls, v):
        if v not in TICKET_PROVIDERS:
            raise ValueError(f"provider must be one of {', '.join(TICKET_PROVIDERS)}")
        return v

    @field_validator("base_url")
    @classmethod
    def _url(cls, v):
        p = urlparse(v.strip())
        if p.scheme != "https" or not p.hostname:
            raise ValueError("base_url must be an https:// URL")
        return v.strip().rstrip("/")

    @field_validator("auto_create_min_severity")
    @classmethod
    def _sev(cls, v):
        if v is not None and v not in SEVERITY_RANK:
            raise ValueError(f"auto_create_min_severity must be one of {', '.join(SEVERITY_RANK)}")
        return v

    @field_validator("settings")
    @classmethod
    def _settings(cls, v):
        extra = set(v) - SETTING_KEYS
        if extra:
            raise ValueError(f"unknown settings: {', '.join(sorted(extra))}")
        return v


def _out(c: Optional[TicketConnection]) -> Optional[dict]:
    if c is None:
        return None
    return {"provider": c.provider, "base_url": c.base_url, "settings": c.settings, "enabled": c.enabled,
            "auto_create_min_severity": c.auto_create_min_severity, "push_notes": c.push_notes,
            "has_credentials": bool(c.credentials_enc), "last_synced_at": c.last_synced_at,
            "last_error": c.last_error}


async def _conn(db: AsyncSession, tenant_id: int) -> Optional[TicketConnection]:
    return (await db.execute(select(TicketConnection).where(TicketConnection.tenant_id == tenant_id))).scalars().first()


async def _case(db: AsyncSession, case_id: int, tenant_id: int) -> Case:
    c = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
    if not c:
        raise HTTPException(status_code=404, detail="Case not found")
    return c


@router.get("/ticketing/connection")
async def get_connection(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    return _out(await _conn(db, tenant_id))


@router.put("/ticketing/connection")
async def put_connection(
    body: ConnectionIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    conn = await _conn(db, tenant_id)
    need_creds, need_settings = REQUIRED[body.provider]
    if body.provider == "jira" and body.settings.get("deployment") != "datacenter":
        need_creds = need_creds | {"email"}
    missing = sorted(k for k in need_settings if not str(body.settings.get(k) or "").strip())
    # Stored credentials only carry over to the same provider at the same host: otherwise an admin could point
    # the saved secrets at a host of their choosing and press Test.
    same_target = conn is not None and conn.provider == body.provider and \
        (urlparse(conn.base_url).hostname or "").lower() == (urlparse(body.base_url).hostname or "").lower()
    if body.credentials is None and not same_target:
        raise HTTPException(status_code=422, detail="credentials are required when the provider or host changes")
    if body.credentials is not None:
        missing += sorted(k for k in need_creds if not (body.credentials.get(k) or "").strip())
    if missing:
        raise HTTPException(status_code=422, detail=f"missing: {', '.join(missing)}")
    before = _out(conn)
    if conn is None:
        conn = TicketConnection(tenant_id=tenant_id)
        db.add(conn)
    if body.auto_create_min_severity and (not conn.auto_create_min_severity or conn.auto_create_since is None):
        conn.auto_create_since = datetime.now(timezone.utc)  # only cases created from now on, never the backlog
    for k in ("provider", "base_url", "settings", "enabled", "auto_create_min_severity", "push_notes"):
        setattr(conn, k, getattr(body, k))
    if body.credentials is not None:
        conn.credentials_enc = ticket_sync.encrypt_credentials(body.credentials)
    conn.last_error = None
    await db.flush()
    after = _out(conn)
    await create_audit_log(db=db, entity_type="ticket_connection", entity_id=conn.id,
                           action="create" if before is None else "update", tenant_id=tenant_id,
                           user_id=current_user.id,
                           changes={k: {"from": (before or {}).get(k), "to": after[k]} for k in
                                    ("provider", "base_url", "settings", "enabled", "auto_create_min_severity",
                                     "push_notes") if (before or {}).get(k) != after[k]}
                           | ({"credentials": "replaced"} if body.credentials is not None else {}))
    await db.commit()
    await db.refresh(conn)
    return _out(conn)


@router.delete("/ticketing/connection", status_code=204)
async def delete_connection(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    """Existing case links keep their ticket URL but stop syncing."""
    conn = await _conn(db, tenant_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="No ticketing connection")
    await create_audit_log(db=db, entity_type="ticket_connection", entity_id=conn.id, action="delete",
                           tenant_id=tenant_id, user_id=current_user.id, changes={"provider": conn.provider})
    await db.delete(conn)
    await db.commit()


@router.post("/ticketing/connection/test")
async def test_connection(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Authenticate and read the configured project/board without creating anything."""
    conn = await _conn(db, tenant_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="No ticketing connection")
    try:
        await ticket_sync.client(conn, await ticket_sync._allowlist(db, tenant_id)).check()
    except (TicketError, KeyError, ValueError) as e:
        return {"ok": False, "error": str(e) if isinstance(e, TicketError) else f"check your settings ({e})"}
    return {"ok": True}


def _link_out(link, provider: Optional[str]) -> Optional[dict]:
    if link is None:
        return None
    return {"provider": link.system, "label": PROVIDER_LABEL.get(link.system, link.system),
            "external_id": link.external_id, "url": link.url, "remote_status": link.remote_status,
            "remote_closed": link.remote_closed, "last_synced_at": link.last_synced_at,
            "syncing": provider is not None}


@router.get("/cases/{case_id}/ticket")
async def get_case_ticket(
    case_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _case(db, case_id, tenant_id)
    conn = await _conn(db, tenant_id)
    link = await ticket_sync.link_for(db, case_id, tenant_id)
    return {"configured": bool(conn and conn.enabled), "provider": conn.provider if conn else None,
            "ticket": _link_out(link, conn.provider if conn and conn.enabled else None)}


@router.post("/cases/{case_id}/ticket", status_code=201)
async def create_case_ticket(
    case_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    case = await _case(db, case_id, tenant_id)
    conn = await _conn(db, tenant_id)
    if conn is None or not conn.enabled:
        raise HTTPException(status_code=409, detail="Ticketing is not set up for this organization")
    try:
        link = await ticket_sync.create_ticket(db, conn=conn, case=case, user_id=current_user.id)
    except TicketError as e:
        await db.rollback()
        raise HTTPException(status_code=502, detail=str(e))
    await db.commit()
    await db.refresh(link)
    return _link_out(link, conn.provider)


@router.post("/cases/{case_id}/ticket/sync")
async def sync_case_ticket(
    case_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Pull status and comments (and push notes, when enabled) for this case now."""
    case = await _case(db, case_id, tenant_id)
    conn = await _conn(db, tenant_id)
    link = await ticket_sync.link_for(db, case_id, tenant_id)
    if conn is None or not conn.enabled or link is None or link.connection_id != conn.id:
        raise HTTPException(status_code=409, detail="This case has no synced ticket")
    try:
        await ticket_sync.sync_link(db, ticket_sync.client(conn, await ticket_sync._allowlist(db, tenant_id)),
                                    conn, link, case)
    except TicketError as e:
        await db.rollback()
        raise HTTPException(status_code=502, detail=str(e))
    await db.commit()
    await db.refresh(link)
    return _link_out(link, conn.provider)
