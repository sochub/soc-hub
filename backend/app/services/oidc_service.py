"""OIDC relying-party client: discovery, authorization URL, code exchange, ID-token verification.

The only network calls are ``_get_json`` and ``_post_form``; both run the SSRF guard first,
because the issuer is tenant-admin supplied and discovery documents name further URLs.
"""
import base64
import hashlib
import secrets
import socket
import time
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from app.core.config import settings
from app.workflows.ssrf import SSRFError, assert_url_allowed

_TTL = 3600
_TIMEOUT = 10.0

_resolve = socket.getaddrinfo  # module-level hook so tests can stub DNS

_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, dict]] = {}


class OIDCError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _clear_caches() -> None:
    _discovery_cache.clear()
    _jwks_cache.clear()


def _guard(url: str, code: str) -> None:
    if urlparse(url).scheme != "https":
        raise OIDCError(code, "https required")
    try:
        # ponytail: resolve-then-connect leaves a DNS-rebinding window; pin the vetted IP if an IdP ever needs to be untrusted-by-name
        assert_url_allowed(url, settings.OIDC_PRIVATE_HOSTS, resolve=_resolve)
    except SSRFError as e:
        raise OIDCError(code, str(e)) from e


async def _get_json(url: str, code: str = "discovery") -> dict:
    _guard(url, code)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise OIDCError(code, f"request failed: {type(e).__name__}") from e


async def _post_form(url: str, data: dict, code: str = "token") -> dict:
    _guard(url, code)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as c:
            r = await c.post(url, data=data, headers={"Accept": "application/json"})
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise OIDCError(code, f"request failed: {type(e).__name__}") from e


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def _fetch(getter, url: str, code: str) -> dict:
    # _get_json may be replaced in tests by a one-arg callable.
    try:
        return await getter(url)
    except (httpx.HTTPError, ValueError) as e:
        raise OIDCError(code, f"request failed: {type(e).__name__}") from e


async def discover(issuer: str) -> dict:
    issuer = issuer.strip()
    hit = _discovery_cache.get(issuer)
    if hit and hit[0] > time.time():
        return hit[1]
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    meta = await _fetch(_get_json, url, "discovery")
    if not isinstance(meta, dict) or meta.get("issuer") != issuer:
        raise OIDCError("discovery", "issuer mismatch")
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not meta.get(k):
            raise OIDCError("discovery", f"missing {k}")
    _discovery_cache[issuer] = (time.time() + _TTL, meta)
    return meta


def authorization_url(meta: dict, *, client_id: str, redirect_uri: str, scopes: str,
                      state: str, nonce: str, code_challenge: str) -> str:
    q = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    ep = meta["authorization_endpoint"]
    return f"{ep}{'&' if '?' in ep else '?'}{q}"


async def exchange_code(meta: dict, *, client_id: str, client_secret: str, code: str,
                        redirect_uri: str, code_verifier: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": code_verifier,
    }
    try:
        tok = await _post_form(meta["token_endpoint"], data)
    except (httpx.HTTPError, ValueError) as e:
        raise OIDCError("token", f"request failed: {type(e).__name__}") from e
    if not isinstance(tok, dict) or not tok.get("id_token"):
        raise OIDCError("token", "no id_token in response")
    return tok


async def _jwks(uri: str, *, force: bool = False) -> tuple[jwt.PyJWKSet, bool]:
    """Return (keyset, came_from_cache)."""
    hit = _jwks_cache.get(uri)
    if hit and hit[0] > time.time() and not force:
        return jwt.PyJWKSet.from_dict(hit[1]), True
    doc = await _fetch(_get_json, uri, "discovery")
    try:
        ks = jwt.PyJWKSet.from_dict(doc)
    except Exception as e:  # malformed JWKS (PyJWKSetError, KeyError, ...)
        raise OIDCError("discovery", "invalid jwks") from e
    _jwks_cache[uri] = (time.time() + _TTL, doc)
    return ks, False


def _find_key(ks: jwt.PyJWKSet, kid):
    for k in ks.keys:
        if k.key_id == kid:
            return k
    return None


async def verify_id_token(id_token: str, meta: dict, *, client_id: str, nonce: str) -> dict:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as e:
        raise OIDCError("id_token", "malformed token") from e
    kid = header.get("kid")
    uri = meta["jwks_uri"]
    ks, cached = await _jwks(uri)
    key = _find_key(ks, kid)
    if key is None and cached:
        ks, _ = await _jwks(uri, force=True)
        key = _find_key(ks, kid)
    if key is None:
        raise OIDCError("id_token", "unknown signing key")
    try:
        claims = jwt.decode(
            id_token, key.key, algorithms=["RS256", "ES256"], audience=client_id,
            issuer=meta["issuer"], leeway=60,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as e:
        raise OIDCError("id_token", f"invalid token: {type(e).__name__}") from e
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")).encode(), nonce.encode()):
        raise OIDCError("id_token", "nonce mismatch")
    return claims


def claims_email(claims: dict) -> tuple[str, str]:
    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        raise OIDCError("email", "no email claim")
    if claims.get("email_verified") is False:
        raise OIDCError("unverified", "email not verified")
    name = claims.get("name") or " ".join(
        p for p in (claims.get("given_name"), claims.get("family_name")) if p
    ) or ""
    return email.strip().lower(), str(name).strip()
