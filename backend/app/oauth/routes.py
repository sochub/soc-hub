"""OAuth authorization server: metadata (RFC 8414), dynamic client registration (RFC 7591) and the
authorization endpoint with its consent screen (authorization code + PKCE S256)."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.audit.events import audit_event
from app.auth import sessions
from app.auth.login_policy import AMR_PWD
from app.auth.login_tenant import session_may_enter
from app.core.config import settings
from app.core.login_throttle import client_ip, get_login_throttle
from app.models.membership import TenantMembership
from app.models.auth_session import AuthSession
from app.models.oauth_client import OAuthClient
from app.models.tenant import Tenant
from app.models.user import User
from app.oauth.resources import RESOURCES, Resource, issuer, resource_by_url
from app.oauth.tokens import ACCESS_TOKEN_SECONDS, issue_oauth_access_token, tenant_role

router = APIRouter()

REGISTRATIONS_PER_HOUR = 20
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_DENIED_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob", "http", "https", "ws", "wss", "ftp"}
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*$")


def _oauth_redis():
    """Async Redis client (same lazy factory as the login throttle); tests monkeypatch this."""
    return get_login_throttle().redis


def redirect_uri_allowed(uri: str) -> bool:
    if not isinstance(uri, str) or not uri or len(uri) > 2000:
        return False
    if "\\" in uri or "#" in uri or any(ord(c) < 0x21 or ord(c) == 0x7F for c in uri):
        return False
    try:
        p = urlparse(uri)
        host = p.hostname
        p.port  # noqa: B018 — raises ValueError on an invalid port
    except ValueError:
        return False
    if p.fragment or p.username is not None or p.password is not None:
        return False
    scheme = p.scheme.lower()
    if scheme == "https":
        return bool(host)
    if scheme == "http":
        return host in _LOOPBACK
    return bool(_SCHEME_RE.match(scheme)) and scheme not in _DENIED_SCHEMES


def metadata() -> dict[str, Any]:
    base = issuer()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/api/v1/oauth/authorize",
        "token_endpoint": f"{base}/api/v1/oauth/token",
        "registration_endpoint": f"{base}/api/v1/oauth/register",
        "revocation_endpoint": f"{base}/api/v1/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": sorted({s for r in RESOURCES.values() for s in r.scopes}),
    }


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    client_name: Any = None
    redirect_uris: Any = None
    grant_types: Optional[list[str]] = None
    response_types: Optional[list[str]] = None
    token_endpoint_auth_method: Optional[str] = None


def _err(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status)


@router.post("/register", status_code=201)
async def register(body: RegisterIn, request: Request, db: AsyncSession = Depends(deps.get_db)):
    ip = client_ip(request)
    key = f"oauth:reg:{ip}"
    redis = _oauth_redis()
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 3600)
    if count > REGISTRATIONS_PER_HOUR:
        return _err("too_many_requests", "Too many registrations; try again later.", 429)

    name = body.client_name
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
        return _err("invalid_client_metadata", "client_name is required (1-200 characters).")
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in name):
        return _err("invalid_client_metadata", "client_name contains control or formatting characters.")
    uris = body.redirect_uris
    if not isinstance(uris, list) or not 1 <= len(uris) <= 10 or not all(isinstance(u, str) for u in uris):
        return _err("invalid_redirect_uri", "redirect_uris must be a list of 1-10 strings.")
    if not all(redirect_uri_allowed(u) for u in uris):
        return _err("invalid_redirect_uri", "A redirect URI is not allowed.")
    if body.token_endpoint_auth_method not in (None, "none"):
        return _err("invalid_client_metadata", "Only public clients (token_endpoint_auth_method none) are supported.")
    if body.grant_types is not None and set(body.grant_types) != {"authorization_code", "refresh_token"}:
        return _err("invalid_client_metadata", "grant_types must be authorization_code and refresh_token.")
    if body.response_types is not None and body.response_types != ["code"]:
        return _err("invalid_client_metadata", "response_types must be code.")

    client_id = secrets.token_urlsafe(24)
    db.add(OAuthClient(id=client_id, client_name=name.strip(), redirect_uris=uris, created_ip=ip))
    await db.commit()
    hosts = sorted({urlparse(u).hostname or urlparse(u).scheme for u in uris})
    await audit_event("security", "oauth.client_registered", request=request, tenant_id=None,
                      changes={"client_id": client_id, "client_name": name.strip(), "redirect_hosts": hosts})
    return {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "client_name": name.strip(),
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


# ---------------------------------------------------------------------------------------------------------
# Authorization endpoint + consent
# ---------------------------------------------------------------------------------------------------------

CODE_TTL_SECONDS = 60
_PARAMS = ("response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state",
           "scope", "resource")
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")  # RFC 7636 §4.2 (S256 output is 43 chars)
_MAX_STATE = 1024


class AuthorizeParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    response_type: Optional[str] = None
    client_id: Optional[str] = None
    redirect_uri: Optional[str] = None
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[str] = None
    state: Optional[str] = None
    scope: Optional[str] = None
    resource: Optional[str] = None


class DecisionIn(AuthorizeParams):
    tenant_id: Optional[int] = None
    approve: bool = False


def _query_params(request: Request) -> AuthorizeParams:
    q = request.query_params
    return AuthorizeParams(**{k: q.get(k) for k in _PARAMS})


def _is_loopback(p) -> bool:
    return p.scheme == "http" and p.hostname in _LOOPBACK


def redirect_uri_matches(registered: list, uri: str) -> bool:
    """Exact match against a registered URI; for http loopback URIs only the port may differ (RFC 8252 §7.3)."""
    if not isinstance(uri, str) or not redirect_uri_allowed(uri):
        return False
    if uri in registered:
        return True
    p = urlparse(uri)
    if not _is_loopback(p):
        return False
    for reg in registered:
        r = urlparse(reg)
        if (_is_loopback(r) and r.hostname == p.hostname and r.path == p.path and r.query == p.query
                and r.params == p.params and r.username is None and p.username is None):
            return True
    return False


def _with_params(uri: str, params: dict) -> str:
    """Merge params into the redirect URI's query (its existing query is kept), never by concatenation."""
    p = urlparse(uri)
    query = parse_qsl(p.query, keep_blank_values=True)
    query += [(k, v) for k, v in params.items() if v is not None]
    return urlunparse(p._replace(query=urlencode(query), fragment=""))


class _Fatal(Exception):
    """Unknown client or unregistered redirect URI: answered directly, never redirected."""
    def __init__(self, description: str):
        self.description = description


class _RedirectError(Exception):
    def __init__(self, error: str, description: str):
        self.error, self.description = error, description


@dataclass
class _Validated:
    client: OAuthClient
    redirect_uri: str
    code_challenge: str
    state: Optional[str]
    resource: Resource
    scope: str


async def _validate(db: AsyncSession, p: AuthorizeParams) -> _Validated:
    """Raises _Fatal before the redirect URI is trusted, _RedirectError after."""
    client = await db.get(OAuthClient, p.client_id) if p.client_id and len(p.client_id) <= 48 else None
    if client is None:
        raise _Fatal("Unknown client_id.")
    if not p.redirect_uri or not redirect_uri_matches(list(client.redirect_uris or []), p.redirect_uri):
        raise _Fatal("redirect_uri is not registered for this client.")
    if p.state is not None and len(p.state) > _MAX_STATE:
        raise _Fatal("state is too long.")
    if p.response_type != "code":
        raise _RedirectError("unsupported_response_type", "response_type must be code.")
    if not p.code_challenge or not _CHALLENGE_RE.match(p.code_challenge):
        raise _RedirectError("invalid_request", "A PKCE code_challenge is required.")
    if p.code_challenge_method != "S256":
        raise _RedirectError("invalid_request", "code_challenge_method must be S256.")
    resource = resource_by_url(p.resource)
    if resource is None:
        raise _RedirectError("invalid_request", "Unknown resource.")
    if p.scope is None or not p.scope.strip():
        scopes = list(resource.scopes)
    else:
        scopes = p.scope.split()
        if not set(scopes) <= set(resource.scopes):
            raise _RedirectError("invalid_scope", "Requested scope is not available for this resource.")
    return _Validated(client, p.redirect_uri, p.code_challenge, p.state, resource, " ".join(dict.fromkeys(scopes)))


def _fatal_response(e: _Fatal) -> JSONResponse:
    return _err("invalid_request", e.description)


@router.get("/authorize")
async def authorize(request: Request, db: AsyncSession = Depends(deps.get_db)):
    p = _query_params(request)
    try:
        await _validate(db, p)
    except _Fatal as e:
        return _fatal_response(e)
    except _RedirectError as e:
        return RedirectResponse(_with_params(p.redirect_uri, {"error": e.error, "state": p.state}), status_code=303)
    forward = {k: v for k, v in p.model_dump().items() if v is not None}
    url = f"{settings.FRONTEND_URL.rstrip('/')}/oauth/consent?{urlencode(forward)}"
    return RedirectResponse(url, status_code=303)


def _browser_user(request: Request, user: User = Depends(deps.get_current_active_user)) -> User:
    """Consent is interactive: only the browser session cookie counts, never a bearer token."""
    if getattr(request.state, "auth_via", None) != "cookie":
        raise HTTPException(status_code=401, detail="A browser session is required.")
    return user


async def _tenant_choices(db: AsyncSession, user: User, resource: Resource) -> tuple[list[dict], list[dict]]:
    """Memberships (super admins included: memberships only) whose tenant passes the resource gate, split
    into those this session may enter and SSO-method tenants it can't (sign in with their SSO first)."""
    rows = (await db.execute(
        select(Tenant, TenantMembership.role)
        .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
        .where(TenantMembership.user_id == user.id, Tenant.is_active.isnot(False))
        .order_by(Tenant.id))).all()
    tenants, sso_tenants = [], []
    for tenant, role in rows:
        if not resource.tenant_gate(tenant):
            continue
        if session_may_enter(user, tenant.id, tenant.member_login_method):
            tenants.append({"id": tenant.id, "name": tenant.name, "role": role})
        elif tenant.member_login_method != "password":
            sso_tenants.append({"id": tenant.id, "name": tenant.name, "slug": tenant.slug})
    return tenants, sso_tenants


async def _validated_or_400(db: AsyncSession, p: AuthorizeParams) -> _Validated:
    try:
        return await _validate(db, p)
    except (_Fatal, _RedirectError) as e:
        raise HTTPException(status_code=400, detail={"error": getattr(e, "error", "invalid_request"),
                                                     "error_description": e.description})


@router.get("/authorize/info")
async def authorize_info(request: Request, db: AsyncSession = Depends(deps.get_db),
                         user: User = Depends(_browser_user)):
    v = await _validated_or_400(db, _query_params(request))
    tenants, sso_tenants = await _tenant_choices(db, user, v.resource)
    return {
        "client_name": v.client.client_name,
        "redirect_host": urlparse(v.redirect_uri).hostname or urlparse(v.redirect_uri).scheme,
        "unverified": True,
        "resource": v.resource.url,
        "scope": v.scope,
        "tenants": tenants,
        "sso_tenants": sso_tenants,
    }


@router.post("/authorize/decision")
async def authorize_decision(body: DecisionIn, request: Request, db: AsyncSession = Depends(deps.get_db),
                             user: User = Depends(_browser_user)):
    v = await _validated_or_400(db, body)
    if not body.approve:
        await audit_event("security", "oauth.consent_denied", request=request, user_id=user.id,
                          user_email=user.email, tenant_id=None,
                          changes={"client_id": v.client.id, "tenant_id": body.tenant_id})
        return {"redirect": _with_params(v.redirect_uri, {"error": "access_denied", "state": v.state})}
    tenants, _ = await _tenant_choices(db, user, v.resource)
    if body.tenant_id is None or body.tenant_id not in {t["id"] for t in tenants}:
        raise HTTPException(status_code=403, detail="This organization is not available for this app.")
    code = secrets.token_urlsafe(32)
    payload = {
        "client_id": v.client.id, "redirect_uri": v.redirect_uri, "code_challenge": v.code_challenge,
        "user_id": user.id, "tenant_id": body.tenant_id, "resource": v.resource.url, "scope": v.scope,
        "amr": getattr(user, "_amr", AMR_PWD), "sso_tenant_id": getattr(user, "_sso_tenant_id", None),
    }
    await _oauth_redis().set(f"oauth:code:{code}", json.dumps(payload), ex=CODE_TTL_SECONDS)
    await audit_event("security", "oauth.consent_granted", request=request, user_id=user.id,
                      user_email=user.email, tenant_id=body.tenant_id,
                      changes={"client_id": v.client.id, "tenant_id": body.tenant_id})
    return {"redirect": _with_params(v.redirect_uri, {"code": code, "state": v.state})}


# ---------------------------------------------------------------------------------------------------------
# Token endpoint (RFC 6749 §3.2, PKCE RFC 7636) and revocation (RFC 7009)
# ---------------------------------------------------------------------------------------------------------

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_SID_RE = re.compile(r"^[0-9a-f]{32}$")


def _token_err(error: str, description: str) -> JSONResponse:
    status = 401 if error == "invalid_client" else 400
    headers = {**_NO_STORE, **({"WWW-Authenticate": "Bearer"} if status == 401 else {})}
    return JSONResponse({"error": error, "error_description": description}, status_code=status, headers=headers)


def _form_str(form, key: str) -> Optional[str]:
    v = form.get(key)
    return v if isinstance(v, str) and v else None


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not _VERIFIER_RE.match(verifier) or not isinstance(challenge, str):
        return False
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(computed, challenge)


def _token_body(row: AuthSession, user: User, refresh_token: Optional[str]) -> JSONResponse:
    body = {"access_token": issue_oauth_access_token(row, user), "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_SECONDS, "scope": row.scope or ""}
    if refresh_token is not None:  # None: benign concurrent refresh, the client keeps its current one
        body["refresh_token"] = refresh_token
    return JSONResponse(body, headers=_NO_STORE)


async def _load_user(db: AsyncSession, user_id) -> Optional[User]:
    return (await db.execute(select(User).where(User.id == user_id)
                             .execution_options(populate_existing=True))).scalar_one_or_none()


def _same(a: str, b: str) -> bool:
    """Constant-time string equality that also accepts non-ASCII input (compare_digest on str does not)."""
    return hmac.compare_digest(a.encode(), b.encode())


async def _exchange_code(request: Request, db: AsyncSession, form) -> JSONResponse:
    code, redirect_uri = _form_str(form, "code"), _form_str(form, "redirect_uri")
    client_id, verifier = _form_str(form, "client_id"), _form_str(form, "code_verifier")
    if not (code and redirect_uri and client_id and verifier) or len(code) > 128:
        return _token_err("invalid_request", "code, redirect_uri, client_id and code_verifier are required.")
    raw = await _oauth_redis().getdel(f"oauth:code:{code}")  # single use: any attempt burns the code
    if raw is None:
        return _token_err("invalid_grant", "The authorization code is invalid or expired.")
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        return _token_err("invalid_grant", "The authorization code is invalid or expired.")
    if (not _same(str(stored.get("client_id", "")), client_id)
            or stored.get("redirect_uri") != redirect_uri
            or not _pkce_ok(verifier, stored.get("code_challenge"))):
        return _token_err("invalid_grant", "The authorization code does not match this request.")
    client = await db.get(OAuthClient, client_id)
    if client is None:
        return _token_err("invalid_client", "Unknown client.")
    user = await _load_user(db, stored.get("user_id"))
    amr, sso_tid, tenant_id = stored.get("amr") or AMR_PWD, stored.get("sso_tenant_id"), stored.get("tenant_id")
    if user is None or await tenant_role(db, user, tenant_id=tenant_id, resource_url=stored.get("resource"),
                                         amr=amr, sso_tenant_id=sso_tid) is None:
        return _token_err("invalid_grant", "Access to this organization is no longer allowed.")
    row, refresh_token = await sessions.create_session(
        db, user, kind="oauth", amr=amr, sso_tenant_id=sso_tid, tenant_id=tenant_id, client_id=client_id,
        resource=stored.get("resource"), scope=stored.get("scope"), request=request)
    await db.commit()
    await audit_event("security", "oauth.token_issued", request=request, user_id=user.id, user_email=user.email,
                      tenant_id=tenant_id, changes={"client_id": client_id, "tenant_id": tenant_id})
    return _token_body(row, user, refresh_token)


async def _refresh(request: Request, db: AsyncSession, form) -> JSONResponse:
    presented, client_id = _form_str(form, "refresh_token"), _form_str(form, "client_id")
    if not presented or not client_id:
        return _token_err("invalid_request", "refresh_token and client_id are required.")
    sid = presented.partition(".")[0]
    owner = (await db.execute(select(AuthSession.client_id).where(
        AuthSession.id == sid, AuthSession.kind == "oauth"))).first() if _SID_RE.match(sid) else None
    # A refresh token presented by another client is refused before it can rotate anything.
    if owner is None or not _same(str(owner[0] or ""), client_id):
        return _token_err("invalid_grant", "The refresh token is invalid.")
    try:
        row, user, new_refresh = await sessions.rotate(db, presented, kind="oauth")
    except sessions.RefreshError:
        return _token_err("invalid_grant", "The refresh token is invalid, expired or revoked.")
    if await tenant_role(db, user, tenant_id=row.tenant_id, resource_url=row.resource, amr=row.amr,
                         sso_tenant_id=row.sso_tenant_id) is None:
        await sessions.revoke(db, row.id, user_id=row.user_id)
        await db.commit()
        return _token_err("invalid_grant", "Access to this organization is no longer allowed.")
    await db.commit()
    return _token_body(row, user, new_refresh)


@router.post("/token")
async def token(request: Request, db: AsyncSession = Depends(deps.get_db)):
    try:
        form = await request.form()
    except Exception:
        return _token_err("invalid_request", "The request body must be form-encoded.")
    grant_type = _form_str(form, "grant_type")
    if grant_type is None:
        return _token_err("invalid_request", "grant_type is required.")
    if grant_type == "authorization_code":
        return await _exchange_code(request, db, form)
    if grant_type == "refresh_token":
        return await _refresh(request, db, form)
    return _token_err("unsupported_grant_type", "Only authorization_code and refresh_token are supported.")


@router.post("/revoke")
async def revoke(request: Request, db: AsyncSession = Depends(deps.get_db)):
    """RFC 7009: always 200. Revokes the session only when the refresh token's secret and the client match."""
    ok = JSONResponse({}, headers=_NO_STORE)
    try:
        form = await request.form()
    except Exception:
        return ok
    presented, client_id = _form_str(form, "token"), _form_str(form, "client_id")
    if not presented or not client_id:
        return ok
    sid, _, secret = presented.partition(".")
    if not _SID_RE.match(sid) or not secret:
        return ok
    row = (await db.execute(select(AuthSession).where(
        AuthSession.id == sid, AuthSession.kind == "oauth", AuthSession.revoked_at.is_(None)))).scalar_one_or_none()
    if row is None or not _same(str(row.client_id or ""), client_id):
        return ok
    h = sessions.hash_secret(secret)
    if not (hmac.compare_digest(h, row.refresh_hash)
            or (row.prev_refresh_hash is not None and hmac.compare_digest(h, row.prev_refresh_hash))):
        return ok
    user_id, tenant_id = row.user_id, row.tenant_id
    if await sessions.revoke(db, sid, user_id=user_id):
        await db.commit()
        await audit_event("security", "oauth.token_revoked", request=request, user_id=user_id,
                          tenant_id=tenant_id, changes={"client_id": client_id, "session_id": sid})
    return ok
