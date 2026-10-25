"""OAuth authorization server: metadata (RFC 8414) and dynamic client registration (RFC 7591)."""
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.audit.events import audit_event
from app.core.login_throttle import client_ip, get_login_throttle
from app.models.oauth_client import OAuthClient
from app.oauth.resources import RESOURCES, issuer

router = APIRouter()

REGISTRATIONS_PER_HOUR = 20
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_DENIED_SCHEMES = {"javascript", "data", "file", "vbscript", "about", "blob", "http", "https", "ws", "wss", "ftp"}
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*$")


def _oauth_redis():
    """Async Redis client (same lazy factory as the login throttle); tests monkeypatch this."""
    return get_login_throttle().redis


def redirect_uri_allowed(uri: str) -> bool:
    try:
        p = urlparse(uri)
        host = p.hostname
    except ValueError:
        return False
    if p.fragment or "#" in uri:
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
