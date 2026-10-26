import json
import logging
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.v1.auth import _safe_domain
from app.audit.events import audit_event
from app.auth import web
from app.auth.login_policy import AMR_OIDC, AMR_SAML
from app.core import security
from app.core.config import settings
from app.core.login_throttle import get_login_throttle
from app.guests import tokens as guest_tokens
from app.guests.access import ACTIVE as GUEST_ACTIVE, domain_allowed
from app.models.case_guest import CaseGuest
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user import User
from app.schemas.sso import SSOConfigOut, SSOConfigUpdate
from app.services import oidc_service, saml_service
from app.utils.audit import create_audit_log
from app.utils.crypto import decrypt, encrypt

# Public SAML SP endpoints (mounted under /auth)
saml_router = APIRouter()
# Tenant-admin config endpoints (mounted under /tenants, registered before
# tenants.router so /sso-config isn't swallowed by /{tenant_id})
sso_admin_router = APIRouter()

_ALLOWED_JIT_ROLES = {"analyst", "viewer"}
logger = logging.getLogger(__name__)
SSO_FAILED = "SSO sign-in failed: invalid SAML response"
# python3-saml accepts an assertion until NotOnOrAfter + ALLOWED_CLOCK_DRIFT, so the replay key must outlive that.
# No NotOnOrAfter means no library time bound at all: keep the key for the maximum.
_AID_MARGIN = OneLogin_Saml2_Constants.ALLOWED_CLOCK_DRIFT + 60
_AID_MIN_TTL, _AID_MAX_TTL = _AID_MARGIN, 86400


def _saml_redis():
    """Async Redis client (same lazy factory as the login throttle); tests monkeypatch this."""
    return get_login_throttle().redis


def _oidc_redis():
    """Async Redis client for OIDC login state (same lazy factory); tests monkeypatch this."""
    return get_login_throttle().redis


async def _claim_assertion(tenant_id: int, auth) -> bool:
    """Record the assertion ID once (SET NX). False on a replay, a missing ID or Redis trouble (fail closed)."""
    aid = auth.get_last_assertion_id()
    if not aid:
        logger.warning("SAML assertion without an ID rejected tenant=%s", tenant_id)
        return False
    not_after = auth.get_last_assertion_not_on_or_after()
    ttl = int(not_after - time.time()) + _AID_MARGIN if not_after else _AID_MAX_TTL
    ttl = max(_AID_MIN_TTL, min(_AID_MAX_TTL, ttl))
    try:
        ok = await _saml_redis().set(f"saml_aid:{tenant_id}:{aid}", "1", nx=True, ex=ttl)
    except Exception:
        logger.exception("SAML replay check unavailable; refusing sign-in tenant=%s", tenant_id)
        return False
    if not ok:
        logger.warning("SAML assertion replay rejected tenant=%s", tenant_id)
        return False
    return True


def _error_redirect(message: str) -> RedirectResponse:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return RedirectResponse(f"{base}/login#sso_error={quote(message)}", status_code=303)


GUEST_RELAY = "guest"
GUEST_SSO_DENIED = "This account has no active guest access here."
_SLUG_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def _safe_slug_for_redirect(slug: str) -> str:
    value = (slug or "").strip().lower()
    return value if _SLUG_RE.fullmatch(value) else ""


def _guest_error_redirect(tenant: Optional[Tenant], message: str) -> RedirectResponse:
    """Back to the guest login with the error. `?t=` (which shows the "Sign in with SSO" retry button) is built
    from the tenant row loaded from the DB, never from the request, and only when that tenant's SSO is usable."""
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    slug = _safe_slug_for_redirect(tenant.slug) if tenant is not None else ""
    retry = f"?t={quote(slug, safe='')}" if slug else ""
    return RedirectResponse(f"{base}/guest/login{retry}#sso_error={quote(message)}", status_code=303)


async def _guest_acs(request: Request, db: AsyncSession, tenant: Tenant, email: str,
                     proto: str = "saml") -> RedirectResponse:
    """The IdP (`proto`: saml|oidc) proves `email`; issue a guest session only if it has an active grant in
    THIS tenant and the tenant's guests sign in with that protocol. Never touches users/memberships."""
    if tenant.guest_login_method != proto:
        return await _acs_fail(request, tenant, "This organization's guests sign in another way.", "not_enabled",
                               guest=True, retry_tenant=tenant)
    email = email.strip().lower()
    grants = (await db.execute(select(CaseGuest).join(Tenant, Tenant.id == CaseGuest.tenant_id).where(
        CaseGuest.tenant_id == tenant.id, CaseGuest.email == email, *GUEST_ACTIVE()))).scalars().all()
    if not grants or not domain_allowed(tenant, email):
        dom = _safe_domain(email)
        await audit_event("security", "guest.login.sso", request=request, outcome="denied", tenant_id=tenant.id,
                          changes={"email_domain": dom} if dom else None)
        return _guest_error_redirect(tenant, GUEST_SSO_DENIED)
    for g in grants:
        await create_audit_log(db=db, entity_type="case_guest", entity_id=g.id, action="guest_sso_login",
                               tenant_id=tenant.id, changes={"actor": f"guest:{email}"})
    await db.commit()
    await audit_event("security", "guest.login.sso", request=request, actor=f"guest:{email}", tenant_id=tenant.id)
    token = guest_tokens.issue_session(email, "sso", sso_tenant_id=tenant.id, sso_proto=proto)
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return RedirectResponse(f"{base}/guest/sso#token={quote(token)}", status_code=303)


async def _tenant_and_config(db: AsyncSession, slug: str):
    tenant = (await db.execute(
        select(Tenant).where(Tenant.slug == slug, Tenant.is_active == True)  # noqa: E712
    )).scalars().first()
    if not tenant:
        return None, None
    cfg = (await db.execute(
        select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == tenant.id)
    )).scalars().first()
    return tenant, cfg


def saml_ready(cfg: Optional[TenantSSOConfig]) -> bool:
    """SAML config complete (the login-method settings, not `cfg.enabled`, decide whether it is used)."""
    return bool(cfg and cfg.idp_entity_id and cfg.idp_sso_url and cfg.idp_x509_cert)


def oidc_ready(cfg: Optional[TenantSSOConfig]) -> bool:
    """OIDC config complete (issuer, client ID and client secret)."""
    return bool(cfg and cfg.oidc_issuer and cfg.oidc_client_id and cfg.oidc_client_secret_enc)


_READY = {"saml": saml_ready, "oidc": oidc_ready}


def _method(tenant: Tenant, guest: bool) -> str:
    return tenant.guest_login_method if guest else tenant.member_login_method


def _guest_retry(tenant: Optional[Tenant], cfg: Optional[TenantSSOConfig]) -> Optional[Tenant]:
    """The tenant to offer a guest "Sign in with SSO" retry for: only when its guest SSO method is usable."""
    if tenant is None:
        return None
    ready = _READY.get(tenant.guest_login_method)
    return tenant if ready and ready(cfg) else None


def oidc_redirect_uri(slug: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/v1/auth/oidc/{slug}/callback"


SSO_NOT_ALLOWED = ("This account can't use single sign-on for this organization — "
                   "sign in with your password.")


async def _resolve_sso_user(
    db: AsyncSession, tenant: Tenant, cfg: TenantSSOConfig, email: str, full_name: str
) -> User:
    """Find-or-provision the SSO user per the tenant's JIT settings.

    A tenant's IdP is controlled by that tenant's admins, so it may only sign
    in accounts that belong to this tenant alone. An existing user is refused
    (HTTPException 403) when they are a super admin, belong to any other
    tenant, or are not a member of this tenant (no auto-attach). Auto-provision
    only ever creates NEW users (with a membership in this tenant)."""
    email = email.strip().lower()
    role = cfg.default_role if cfg.default_role in _ALLOWED_JIT_ROLES else "viewer"

    user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalars().first()
    if user and not user.is_active:
        raise HTTPException(status_code=403, detail="Your account is deactivated.")

    if not user:
        if not cfg.auto_provision:
            raise HTTPException(
                status_code=403,
                detail="No account for this email. Ask your admin for an invitation.",
            )
        user = User(
            email=email,
            # Unusable random password: SSO-provisioned users sign in via the IdP.
            hashed_password=security.get_password_hash(secrets.token_urlsafe(32)),
            full_name=full_name or email.split("@")[0],
            is_active=True,
            is_super_admin=False,
            password_login_enabled=False,
        )
        db.add(user)
        await db.flush()
        db.add(TenantMembership(user_id=user.id, tenant_id=tenant.id, role=role))
        await create_audit_log(db=db, entity_type="user", entity_id=user.id,
                               action="sso_provisioned", tenant_id=tenant.id,
                               user_id=user.id, changes={"role": role})
        await db.commit()
        return user

    if user.is_super_admin:
        raise HTTPException(status_code=403, detail=SSO_NOT_ALLOWED)
    tenant_ids = set((await db.execute(
        select(TenantMembership.tenant_id).where(TenantMembership.user_id == user.id)
    )).scalars().all())
    if tenant_ids != {tenant.id}:
        # Not a member here: never auto-attached. A member of other tenants too is refused: this tenant's admins
        # control the IdP and could otherwise sign in as an account they don't own (and change its account-wide
        # state: sessions, profile). Same rule as app.utils.account_guard.
        raise HTTPException(status_code=403, detail=SSO_NOT_ALLOWED)
    return user


@saml_router.get("/saml/{slug}/metadata")
async def saml_metadata(*, db: AsyncSession = Depends(deps.get_db), slug: str) -> Any:
    """SP metadata XML for the tenant — paste/import into the IdP. Public."""
    tenant, cfg = await _tenant_and_config(db, slug)
    if not tenant or not cfg:
        raise HTTPException(status_code=404, detail="SSO is not configured for this tenant.")
    xml = saml_service.sp_metadata_xml(slug, cfg)
    return Response(content=xml, media_type="application/xml")


@saml_router.get("/saml/{slug}/login")
async def saml_login(*, db: AsyncSession = Depends(deps.get_db), slug: str, guest: bool = Query(False)) -> Any:
    """SP-initiated login: redirect the browser to the tenant's IdP. Public. `guest=1` starts a guest sign-in."""
    tenant, cfg = await _tenant_and_config(db, slug)
    if not tenant or not saml_ready(cfg) or _method(tenant, guest) != "saml":
        return _guest_error_redirect(None, "SSO is not enabled for this organization.") if guest else \
            _error_redirect("SSO is not enabled for this tenant.")
    req = saml_service.build_request_dict({}, {}, f"/api/v1/auth/saml/{slug}/login")
    auth = saml_service.make_auth(slug, cfg, req)
    return RedirectResponse(auth.login(return_to=GUEST_RELAY) if guest else auth.login(), status_code=303)


async def _acs_fail(request: Request, tenant, message: str, code: str, *, guest: bool,
                    retry_tenant: Optional[Tenant], action: str = "sso.failed") -> RedirectResponse:
    """Record the failure (fixed reason code, never IdP text) and send the user back to the right login page."""
    await audit_event("security", action, request=request, outcome="denied",
                      tenant_id=tenant.id if tenant else None, changes={"reason": code})
    return _guest_error_redirect(retry_tenant, message) if guest else _error_redirect(message)


@saml_router.post("/saml/{slug}/acs")
async def saml_acs(*, request: Request, db: AsyncSession = Depends(deps.get_db), slug: str) -> Any:
    """Assertion Consumer Service: validate the IdP response, sign the user in,
    and bounce back to the frontend with the JWT in the URL fragment. Public."""
    form = await request.form()
    is_guest = form.get("RelayState") == GUEST_RELAY

    tenant, cfg = await _tenant_and_config(db, slug)
    ready = tenant is not None and saml_ready(cfg)

    async def fail(msg: str, code: str, action: str = "sso.failed") -> RedirectResponse:
        return await _acs_fail(request, tenant, msg, code, guest=is_guest,
                               retry_tenant=_guest_retry(tenant, cfg), action=action)

    if not ready or _method(tenant, is_guest) != "saml":
        return await fail("SSO is not enabled for this tenant.", "not_enabled")

    req = saml_service.build_request_dict(
        {k: v for k, v in form.items()}, {}, f"/api/v1/auth/saml/{slug}/acs"
    )
    auth = saml_service.make_auth(slug, cfg, req)
    auth.process_response()
    if auth.get_errors() or not auth.is_authenticated():
        reason = auth.get_last_error_reason() or ", ".join(auth.get_errors()) or "invalid SAML response"
        return await fail(f"SSO sign-in failed: {reason}", "saml_invalid")
    if not await _claim_assertion(tenant.id, auth):
        return await fail(SSO_FAILED, "replay", action="sso.replay")

    email = (auth.get_nameid() or "").strip()
    attrs = auth.get_attributes() or {}
    if "@" not in email:
        for key in ("email", "mail", "urn:oid:0.9.2342.19200300.100.1.3",
                    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"):
            vals = attrs.get(key) or []
            if vals and "@" in vals[0]:
                email = vals[0].strip()
                break
    if "@" not in email:
        return await fail("SSO response did not include an email address.", "no_email")

    if is_guest:
        return await _guest_acs(request, db, tenant, email)

    full_name = ""
    for key in ("displayName", "cn", "name",
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name"):
        vals = attrs.get(key) or []
        if vals and vals[0].strip():
            full_name = vals[0].strip()
            break

    return await _finish_member_sso(request, db, tenant, cfg, email, full_name, AMR_SAML)


async def _finish_member_sso(request: Request, db: AsyncSession, tenant: Tenant, cfg: TenantSSOConfig,
                             email: str, full_name: str, amr: str) -> RedirectResponse:
    """The IdP proved `email`: resolve the member, audit, and set a session that opens only this tenant."""
    try:
        user = await _resolve_sso_user(db, tenant, cfg, email, full_name)
    except HTTPException as exc:
        return await _acs_fail(request, tenant, exc.detail, "not_allowed", guest=False, retry_tenant=None)

    await create_audit_log(db=db, entity_type="user", entity_id=user.id, action="sso_login",
                           tenant_id=tenant.id, user_id=user.id, changes={"protocol": amr})
    await db.commit()
    await audit_event("security", "sso.success", request=request, user_id=user.id, user_email=user.email,
                      tenant_id=tenant.id)

    base = settings.PUBLIC_BASE_URL.rstrip("/")
    out = RedirectResponse(f"{base}/login#sso=ok", status_code=303)
    await web.start_web_session(db, request, out, user, tenant.id, amr=amr, sso_tenant_id=tenant.id)
    return out


PASSWORD_TENANT = "This organization signs in with a password."
SSO_NOT_ENABLED = "SSO is not enabled for this organization."
OIDC_FAILED = "SSO sign-in failed. Please try again."
_OIDC_MESSAGES = {
    "state": "Your sign-in session expired or was already used. Please try again.",
    "idp_error": "Your identity provider did not complete the sign-in.",
    "email": "SSO response did not include an email address.",
    "unverified": "Your identity provider has not verified this email address.",
}
_STATE_TTL = 600
_STATE_KEY = "oidc:state:{}"
# Binds the login state to the browser that started it (login CSRF). Lax: the IdP's redirect back is a
# cross-site top-level GET, which Strict would not carry.
OIDC_COOKIE = "sochub_oidc"
_OIDC_COOKIE_PATH = "/api/v1/auth/oidc"


def _oidc_cookie_secure() -> bool:
    return settings.PUBLIC_BASE_URL.startswith("https")


@saml_router.get("/sso/{slug}/login")
async def sso_login(*, db: AsyncSession = Depends(deps.get_db), slug: str, guest: bool = Query(False)) -> Any:
    """Send the browser to the tenant's sign-in protocol for this audience (members, or guests with `guest=1`)."""
    tenant = (await db.execute(
        select(Tenant).where(Tenant.slug == slug, Tenant.is_active == True)  # noqa: E712
    )).scalars().first()
    method = _method(tenant, guest) if tenant else None
    if method not in ("saml", "oidc"):
        msg = PASSWORD_TENANT if method == "password" else SSO_NOT_ENABLED
        return _guest_error_redirect(None, msg) if guest else _error_redirect(msg)
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return RedirectResponse(f"{base}/api/v1/auth/{method}/{quote(tenant.slug, safe='')}/login"
                            + ("?guest=1" if guest else ""), status_code=303)


@saml_router.get("/oidc/{slug}/login")
async def oidc_login(*, request: Request, db: AsyncSession = Depends(deps.get_db), slug: str,
                     guest: bool = Query(False)) -> Any:
    """Start an OIDC authorization-code + PKCE sign-in: store state/nonce/verifier server-side, 303 to the IdP."""
    tenant, cfg = await _tenant_and_config(db, slug)
    if not tenant or not oidc_ready(cfg) or _method(tenant, guest) != "oidc":
        return _guest_error_redirect(None, SSO_NOT_ENABLED) if guest else _error_redirect(SSO_NOT_ENABLED)

    async def fail(code: str) -> RedirectResponse:
        return await _acs_fail(request, tenant, OIDC_FAILED, code, guest=guest,
                               retry_tenant=_guest_retry(tenant, cfg) if guest else None)

    try:
        meta = await oidc_service.discover(cfg.oidc_issuer)
    except oidc_service.OIDCError as exc:
        logger.warning("OIDC discovery failed tenant=%s code=%s", tenant.id, exc.code)
        return await fail("discovery")
    if urlparse(str(meta.get("authorization_endpoint"))).scheme != "https":
        return await fail("discovery")

    state, nonce, bind = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = oidc_service.pkce_pair()
    payload = json.dumps({"tenant_id": tenant.id, "nonce": nonce, "verifier": verifier, "guest": guest,
                          "bind": bind})
    try:
        await _oidc_redis().set(_STATE_KEY.format(state), payload, ex=_STATE_TTL)
    except Exception:
        logger.exception("OIDC state store unavailable; refusing sign-in tenant=%s", tenant.id)
        return await fail("state")
    url = oidc_service.authorization_url(
        meta, client_id=cfg.oidc_client_id, redirect_uri=oidc_redirect_uri(tenant.slug),
        scopes=cfg.oidc_scopes or "openid email profile", state=state, nonce=nonce, code_challenge=challenge)
    out = RedirectResponse(url, status_code=303)
    out.set_cookie(OIDC_COOKIE, bind, max_age=_STATE_TTL, path=_OIDC_COOKIE_PATH, httponly=True, samesite="lax",
                   secure=_oidc_cookie_secure())
    return out


async def _pop_state(state: Optional[str]) -> Optional[dict]:
    """Single-use: GETDEL the stored login state. None when missing, malformed or Redis is down (fail closed)."""
    if not state or len(state) > 128:
        return None
    try:
        raw = await _oidc_redis().getdel(_STATE_KEY.format(state))
    except Exception:
        logger.exception("OIDC state store unavailable")
        return None
    try:
        data = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ok = (isinstance(data.get("tenant_id"), int) and isinstance(data.get("guest"), bool)
          and all(isinstance(data.get(k), str) and data[k] for k in ("nonce", "verifier", "bind")))
    return data if ok else None


@saml_router.get("/oidc/{slug}/callback")
async def oidc_callback(*, request: Request, db: AsyncSession = Depends(deps.get_db), slug: str,
                        code: Optional[str] = Query(None), state: Optional[str] = Query(None),
                        error: Optional[str] = Query(None)) -> Any:
    """OIDC redirect URI: consume the state, exchange the code, verify the ID token, then sign the member or
    guest in exactly like the SAML ACS does. Public."""
    out = await _oidc_callback(request, db, slug, code, state, error)
    out.delete_cookie(OIDC_COOKIE, path=_OIDC_COOKIE_PATH, httponly=True, samesite="lax",
                      secure=_oidc_cookie_secure())
    return out


async def _oidc_callback(request: Request, db: AsyncSession, slug: str, code: Optional[str],
                         state: Optional[str], error: Optional[str]) -> RedirectResponse:
    tenant, cfg = await _tenant_and_config(db, slug)
    data = await _pop_state(state)
    is_guest = bool(data and data["guest"])

    async def fail(code_: str, message: Optional[str] = None) -> RedirectResponse:
        return await _acs_fail(request, tenant, message or _OIDC_MESSAGES.get(code_, OIDC_FAILED), code_,
                               guest=is_guest, retry_tenant=_guest_retry(tenant, cfg) if is_guest else None)

    bound = request.cookies.get(OIDC_COOKIE) or ""
    if (data is None or tenant is None or data["tenant_id"] != tenant.id
            or not secrets.compare_digest(bound.encode(), data["bind"].encode())):
        return await fail("state")
    if error:
        return await fail("idp_error")  # never echo IdP-supplied text
    if not oidc_ready(cfg) or _method(tenant, is_guest) != "oidc":
        return await fail("not_enabled", SSO_NOT_ENABLED)
    if not code:
        return await fail("token")
    try:
        secret = decrypt(cfg.oidc_client_secret_enc)
    except Exception:
        logger.error("OIDC client secret could not be decrypted tenant=%s", tenant.id)
        return await fail("token")
    try:
        meta = await oidc_service.discover(cfg.oidc_issuer)
        tok = await oidc_service.exchange_code(meta, client_id=cfg.oidc_client_id, client_secret=secret, code=code,
                                               redirect_uri=oidc_redirect_uri(tenant.slug),
                                               code_verifier=data["verifier"])
        claims = await oidc_service.verify_id_token(tok["id_token"], meta, client_id=cfg.oidc_client_id,
                                                    nonce=data["nonce"])
        email, full_name = oidc_service.claims_email(claims)
    except oidc_service.OIDCError as exc:
        logger.warning("OIDC sign-in failed tenant=%s code=%s", tenant.id, exc.code)
        return await fail(exc.code if exc.code in ("discovery", "token", "id_token", "email", "unverified")
                          else "token")
    if "@" not in email:
        return await fail("email")

    if is_guest:
        return await _guest_acs(request, db, tenant, email, proto="oidc")
    return await _finish_member_sso(request, db, tenant, cfg, email, full_name, AMR_OIDC)


def _config_out(tenant: Tenant, cfg: Optional[TenantSSOConfig]) -> SSOConfigOut:
    urls = saml_service.sp_urls(tenant.slug)
    return SSOConfigOut(
        member_login_method=tenant.member_login_method,
        guest_login_method=tenant.guest_login_method,
        idp_entity_id=cfg.idp_entity_id if cfg else None,
        idp_sso_url=cfg.idp_sso_url if cfg else None,
        idp_x509_cert=cfg.idp_x509_cert if cfg else None,
        oidc_issuer=cfg.oidc_issuer if cfg else None,
        oidc_client_id=cfg.oidc_client_id if cfg else None,
        oidc_scopes=(cfg.oidc_scopes if cfg and cfg.oidc_scopes else "openid email profile"),
        has_oidc_client_secret=bool(cfg and cfg.oidc_client_secret_enc),
        auto_provision=bool(cfg and cfg.auto_provision),
        default_role=(cfg.default_role if cfg else "viewer"),
        sp_entity_id=urls["entity_id"],
        sp_acs_url=urls["acs_url"],
        sp_metadata_url=urls["metadata_url"],
        sp_login_url=urls["login_url"],
        oidc_redirect_uri=oidc_redirect_uri(tenant.slug),
    )


@sso_admin_router.get("/sso-config", response_model=SSOConfigOut)
async def get_sso_config(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """The active tenant's SSO configuration plus SP values for the IdP side."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cfg = (await db.execute(
        select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == tenant_id)
    )).scalars().first()
    return _config_out(tenant, cfg)


@sso_admin_router.put("/sso-config", response_model=SSOConfigOut)
async def update_sso_config(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: SSOConfigUpdate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create/update the active tenant's SSO configuration and login methods. Admin only."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    cfg = (await db.execute(
        select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == tenant_id)
    )).scalars().first()
    if not cfg:
        cfg = TenantSSOConfig(tenant_id=tenant_id, oidc_scopes="openid email profile")
        db.add(cfg)

    for field in ("idp_entity_id", "idp_sso_url", "idp_x509_cert", "oidc_issuer", "oidc_client_id",
                  "oidc_scopes", "auto_provision", "default_role"):
        value = getattr(body, field)
        if value is not None:
            setattr(cfg, field, value)
    secret_changed = bool(body.oidc_client_secret)
    if secret_changed:
        cfg.oidc_client_secret_enc = encrypt(body.oidc_client_secret)
    for field in ("member_login_method", "guest_login_method"):
        value = getattr(body, field)
        if value is not None:
            setattr(tenant, field, value)

    # Whatever changed, an audience may only use a protocol whose config is complete.
    for audience, method in (("members", tenant.member_login_method), ("guests", tenant.guest_login_method)):
        if method in _READY and not _READY[method](cfg):
            await db.rollback()
            raise HTTPException(
                status_code=422,
                detail=f"Complete the {method.upper()} configuration before using it for {audience}.",
            )

    await create_audit_log(db=db, entity_type="tenant", entity_id=tenant_id,
                           action="sso_config_updated", tenant_id=tenant_id,
                           user_id=current_user.id,
                           changes={"member_login_method": tenant.member_login_method,
                                    "guest_login_method": tenant.guest_login_method,
                                    "auto_provision": cfg.auto_provision,
                                    "oidc_client_secret_changed": secret_changed})
    await db.commit()
    await db.refresh(cfg)
    await db.refresh(tenant)
    return _config_out(tenant, cfg)
