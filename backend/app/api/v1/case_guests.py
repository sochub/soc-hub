"""Analyst-side guest management (one case at a time) and tenant guest settings."""
import re
from datetime import datetime, timedelta, timezone
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.api.v1.sso import _sso_ready, _tenant_and_config
from app.core.config import settings
from app.guests import notify as guest_notify
from app.guests import tokens
from app.guests.access import domain_allowed
from app.models.case import Case, TimelineEvent
from app.models.case_guest import CaseGuest, GuestAccount
from app.models.tenant import LOGIN_METHODS, Tenant
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user import User
from app.schemas import case as case_schema
from app.services.email_service import send_guest_invite_email
from app.utils.audit import create_audit_log
from app.utils.emails import normalize_email

router = APIRouter()            # mounted under /cases
settings_router = APIRouter()   # mounted under /tenants

_UNSHAREABLE_EVENT_TYPES = {"guest_invited"}  # contain other guests' emails
DEFAULT_EXPIRY = timedelta(days=30)
MAX_EXPIRY = timedelta(days=365)
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


class GuestInvite(BaseModel):
    email: str = Field(max_length=320)
    permission: Literal["viewer", "contributor"]
    expires_at: Optional[datetime] = None


class GuestPatch(BaseModel):
    permission: Optional[Literal["viewer", "contributor"]] = None
    expires_at: Optional[datetime] = None


class GuestOut(BaseModel):
    id: int
    case_id: int
    email: str
    permission: str
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    has_account: bool
    active: bool


class ShareIn(BaseModel):
    shared: bool


class GuestSettings(BaseModel):
    domain_allowlist: List[str] = Field(default_factory=list, max_length=50)
    login_method: str = "password"


class GuestSettingsOut(GuestSettings):
    sso_ready: bool        # any SSO method usable
    saml_ready: bool
    oidc_ready: bool


def _out(g: CaseGuest, tenant: Tenant) -> GuestOut:
    now = datetime.now(timezone.utc)
    return GuestOut(id=g.id, case_id=g.case_id, email=g.email, permission=g.permission, expires_at=g.expires_at,
                    revoked_at=g.revoked_at, last_seen_at=g.last_seen_at, created_at=g.created_at,
                    has_account=g.guest_account_id is not None,
                    active=(g.revoked_at is None and g.expires_at > now and bool(tenant.is_active)
                            and domain_allowed(tenant, g.email)))


def _check_expiry(value: Optional[datetime]) -> datetime:
    now = datetime.now(timezone.utc)
    if value is None:
        return now + DEFAULT_EXPIRY
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if value <= now or value > now + MAX_EXPIRY:
        raise HTTPException(status_code=422, detail="Expiry must be in the future and within 365 days")
    return value


async def portal_link(db: AsyncSession, grant: CaseGuest, tenant: Tenant) -> str:
    """Where an email should send this guest: SSO/account login, or a fresh magic link."""
    base = settings.FRONTEND_URL.rstrip("/")
    if tenant.guest_login_method != "password":
        return f"{base}/guest/login?t={tenant.slug}"
    if grant.guest_account_id is not None:  # ?t= offers the SSO button, so only when SSO actually works
        cfg = (await db.execute(select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == tenant.id))
               ).scalars().first()
        return f"{base}/guest/login?t={tenant.slug}" if _sso_ready(cfg) else f"{base}/guest/login"
    return f"{base}/guest/link#{tokens.issue_link(grant.id)}"


async def _case(db: AsyncSession, case_id: int, tid: int) -> Case:
    c = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tid))).scalars().first()
    if c is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return c


async def _grant(db: AsyncSession, case: Case, guest_id: int) -> CaseGuest:
    g = (await db.execute(select(CaseGuest).where(CaseGuest.id == guest_id, CaseGuest.case_id == case.id))
         ).scalars().first()
    if g is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    return g


async def _send_invite(db: AsyncSession, g: CaseGuest, tenant: Tenant, case: Case) -> None:
    link = await portal_link(db, g, tenant)
    send_guest_invite_email(g.email, tenant_name=tenant.name, case_id=case.id, case_title=case.title,
                            permission=g.permission, link=link, sso=tenant.guest_login_method != "password")


@router.post("/{case_id}/guests", response_model=GuestOut, status_code=201)
async def invite_guest(*, case_id: int, body: GuestInvite, db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.require_analyst_or_above),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    case = await _case(db, case_id, tenant_id)
    tenant = await db.get(Tenant, tenant_id)
    email = normalize_email(body.email)
    if not _EMAIL_RE.match(email) or not domain_allowed(tenant, email):
        raise HTTPException(status_code=422, detail="This email domain is not allowed for guests")
    expires = _check_expiry(body.expires_at)
    g = (await db.execute(select(CaseGuest).where(CaseGuest.case_id == case.id, CaseGuest.email == email))
         ).scalars().first()
    if g is None:
        g = CaseGuest(tenant_id=tenant_id, case_id=case.id, email=email)
        db.add(g)
    g.permission, g.expires_at, g.revoked_at, g.invited_by = body.permission, expires, None, current_user.id
    acct = (await db.execute(select(GuestAccount.id).where(GuestAccount.email == email))).scalar()
    g.guest_account_id = acct
    await db.flush()
    db.add(TimelineEvent(case_id=case.id, user_id=current_user.id, event_type="guest_invited",
                         content=f"Guest invited: {email} ({body.permission})"))
    await create_audit_log(db=db, entity_type="case_guest", entity_id=g.id, action="guest_invited",
                           tenant_id=tenant_id, user_id=current_user.id,
                           changes={"case_id": case.id, "email": email, "permission": body.permission})
    await db.commit()
    await db.refresh(g)
    await _send_invite(db, g, tenant, case)
    return _out(g, tenant)


@router.get("/{case_id}/guests", response_model=List[GuestOut])
async def list_guests(*, case_id: int, db: AsyncSession = Depends(deps.get_db),
                      current_user: User = Depends(deps.require_analyst_or_above),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    case = await _case(db, case_id, tenant_id)
    rows = (await db.execute(select(CaseGuest).where(CaseGuest.case_id == case.id).order_by(CaseGuest.id))
            ).scalars().all()
    tenant = await db.get(Tenant, tenant_id)
    return [_out(g, tenant) for g in rows]


@router.patch("/{case_id}/guests/{guest_id}", response_model=GuestOut)
async def update_guest(*, case_id: int, guest_id: int, body: GuestPatch, db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.require_analyst_or_above),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    case = await _case(db, case_id, tenant_id)
    g = await _grant(db, case, guest_id)
    changes = {}
    if body.permission is not None and body.permission != g.permission:
        changes["permission"] = {"from": g.permission, "to": body.permission}
        g.permission = body.permission
    if body.expires_at is not None:
        g.expires_at = _check_expiry(body.expires_at)
        changes["expires_at"] = g.expires_at.isoformat()
    if changes:
        await create_audit_log(db=db, entity_type="case_guest", entity_id=g.id, action="guest_updated",
                               tenant_id=tenant_id, user_id=current_user.id, changes=changes)
    await db.commit()
    await db.refresh(g)
    return _out(g, await db.get(Tenant, tenant_id))


@router.delete("/{case_id}/guests/{guest_id}", status_code=204)
async def revoke_guest(*, case_id: int, guest_id: int, db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.require_analyst_or_above),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    case = await _case(db, case_id, tenant_id)
    g = await _grant(db, case, guest_id)
    if g.revoked_at is None:
        g.revoked_at = datetime.now(timezone.utc)
        await create_audit_log(db=db, entity_type="case_guest", entity_id=g.id, action="guest_revoked",
                               tenant_id=tenant_id, user_id=current_user.id, changes={"email": g.email})
        await db.commit()
    return Response(status_code=204)


@router.post("/{case_id}/guests/{guest_id}/resend", status_code=204)
async def resend_guest_invite(*, case_id: int, guest_id: int, db: AsyncSession = Depends(deps.get_db),
                              current_user: User = Depends(deps.require_analyst_or_above),
                              tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    case = await _case(db, case_id, tenant_id)
    g = await _grant(db, case, guest_id)
    tenant = await db.get(Tenant, tenant_id)
    if not _out(g, tenant).active:
        raise HTTPException(status_code=409, detail="This guest's access is not active — invite them again")
    await _send_invite(db, g, tenant, case)
    return Response(status_code=204)


@router.post("/{case_id}/guests/{guest_id}/reset-mfa", status_code=204)
async def reset_guest_mfa(*, case_id: int, guest_id: int, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    """Recovery for a guest who lost their authenticator: clear TOTP and sign out every session."""
    case = await _case(db, case_id, tenant_id)
    g = await _grant(db, case, guest_id)
    acct = (await db.execute(select(GuestAccount).where(GuestAccount.email == g.email).with_for_update())
            ).scalars().first()
    if acct is None:
        raise HTTPException(status_code=404, detail="This guest has no account")
    foreign = (await db.execute(select(CaseGuest.id).where(CaseGuest.email == acct.email,
                                                           CaseGuest.tenant_id != tenant_id).limit(1))).first()
    if foreign is not None:  # one tenant's admin must not weaken another organization's guest login
        raise HTTPException(status_code=409, detail="This guest account is shared with another organization")
    acct.mfa_enabled_at, acct.mfa_secret_enc = None, None
    acct.token_version = (acct.token_version or 0) + 1
    await create_audit_log(db=db, entity_type="case_guest", entity_id=g.id, action="guest_mfa_reset",
                           tenant_id=tenant_id, user_id=current_user.id, changes={"email": g.email})
    await db.commit()
    return Response(status_code=204)


@router.put("/{case_id}/timeline/{event_id}/share", response_model=case_schema.TimelineEvent)
async def share_timeline_event(*, case_id: int, event_id: int, body: ShareIn, db: AsyncSession = Depends(deps.get_db),
                               current_user: User = Depends(deps.require_analyst_or_above),
                               tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    case = await _case(db, case_id, tenant_id)
    ev = (await db.execute(select(TimelineEvent).where(TimelineEvent.id == event_id,
                                                       TimelineEvent.case_id == case.id))).scalars().first()
    if ev is None:
        raise HTTPException(status_code=404, detail="Timeline event not found")
    if body.shared and ev.event_type in _UNSHAREABLE_EVENT_TYPES:
        raise HTTPException(status_code=422, detail="This entry can't be shared with guests")
    if ev.shared_with_guests != body.shared:
        ev.shared_with_guests = body.shared
        await create_audit_log(db=db, entity_type="timeline_event", entity_id=ev.id,
                               action="guest_shared" if body.shared else "guest_unshared",
                               tenant_id=tenant_id, user_id=current_user.id, changes={"case_id": case.id})
        if body.shared:
            await guest_notify.enqueue(db, case, "shared_entry", f"New update shared on case #{case.id}")
    await db.commit()
    return (await db.execute(select(TimelineEvent).options(selectinload(TimelineEvent.user))
                             .where(TimelineEvent.id == event_id))).scalars().first()


def _oidc_ready(cfg: Optional[TenantSSOConfig]) -> bool:
    return bool(cfg and cfg.oidc_issuer and cfg.oidc_client_id and cfg.oidc_client_secret_enc)


async def _settings_out(db: AsyncSession, tenant: Tenant) -> GuestSettingsOut:
    _, cfg = await _tenant_and_config(db, tenant.slug)
    saml, oidc = _sso_ready(cfg), _oidc_ready(cfg)
    return GuestSettingsOut(domain_allowlist=list(tenant.guest_domain_allowlist or []),
                            login_method=tenant.guest_login_method, sso_ready=saml or oidc,
                            saml_ready=saml, oidc_ready=oidc)


@settings_router.get("/current/guests", response_model=GuestSettingsOut)
async def get_guest_settings(db: AsyncSession = Depends(deps.get_db),
                             current_user: User = Depends(deps.require_admin),
                             tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return await _settings_out(db, await db.get(Tenant, tenant_id))


@settings_router.put("/current/guests", response_model=GuestSettingsOut)
async def update_guest_settings(*, body: GuestSettings, db: AsyncSession = Depends(deps.get_db),
                                current_user: User = Depends(deps.require_admin),
                                tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    tenant = await db.get(Tenant, tenant_id)
    domains = list(dict.fromkeys(d.strip().lower() for d in body.domain_allowlist if d.strip()))
    bad = [d for d in domains if not _DOMAIN_RE.match(d)]
    if bad:
        raise HTTPException(status_code=422, detail=f"Not a valid domain: {bad[0][:100]}")
    if body.login_method not in LOGIN_METHODS:
        raise HTTPException(status_code=422, detail="Unknown login method")
    cur = await _settings_out(db, tenant)
    if body.login_method != "password" and not getattr(cur, f"{body.login_method}_ready"):
        raise HTTPException(status_code=422, detail="Configure and enable that SSO method before requiring it for guests")
    tenant.guest_domain_allowlist, tenant.guest_login_method = domains, body.login_method
    await create_audit_log(db=db, entity_type="tenant", entity_id=tenant.id, action="guest_settings_changed",
                           tenant_id=tenant.id, user_id=current_user.id,
                           changes={"domain_allowlist": domains, "login_method": body.login_method})
    await db.commit()
    return await _settings_out(db, tenant)
