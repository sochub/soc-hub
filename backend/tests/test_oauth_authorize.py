"""OAuth authorize endpoint, consent info and decision (browser session)."""
import json
import secrets
from urllib.parse import parse_qs, urlparse

from sqlalchemy import delete

from app.core.config import settings
from app.core.session_cookie import COOKIE
from app.db.session import AsyncSessionLocal
from app.main import app  # noqa: F401
from app.models.oauth_client import OAuthClient
from app.oauth import routes as oauth_routes
from app.oauth.resources import RESOURCES
from tests.session_fixtures import make_web_token
from tests.test_web_sessions import _go, _tenant, _user

AUTH = "/api/v1/oauth/authorize"
INFO = "/api/v1/oauth/authorize/info"
DECIDE = "/api/v1/oauth/authorize/decision"
XRW = {"X-Requested-With": "XMLHttpRequest"}
REDIRECT = "http://127.0.0.1:33418/cb?keep=1"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


async def _client(ids, redirect_uris=(REDIRECT,)) -> OAuthClient:
    cid = secrets.token_urlsafe(24)
    async with AsyncSessionLocal() as db:
        db.add(OAuthClient(id=cid, client_name="Test Agent", redirect_uris=list(redirect_uris), created_ip="1.1.1.1"))
        await db.commit()
    ids.oauth_clients = getattr(ids, "oauth_clients", []) + [cid]
    return cid


async def _drop_clients(ids):
    cids = getattr(ids, "oauth_clients", [])
    if cids:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(OAuthClient).where(OAuthClient.id.in_(cids)))
            await db.commit()


def _params(cid, **over):
    p = {"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "code_challenge": CHALLENGE,
         "code_challenge_method": "S256", "state": "st-123"}
    p.update(over)
    return {k: v for k, v in p.items() if v is not None}


def _q(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


async def _session(db_user, tenant_id, **kw):
    tok = await make_web_token(None, db_user, tenant_id, **kw)
    return {"Cookie": f"{COOKIE}={tok}"}


async def _setup(ids):
    """User with: t_ok (password, mcp on), t_off (password, mcp off), t_sso (oidc, mcp on)."""
    async with AsyncSessionLocal() as db:
        t_ok = await _tenant(db, ids)
        t_off = await _tenant(db, ids)
        t_sso = await _tenant(db, ids, method="oidc")
        for t in (t_ok, t_sso):
            t.mcp_enabled = True
        await db.commit()
        u = await _user(db, ids, "oa", [(t_ok, "analyst"), (t_off, "analyst"), (t_sso, "analyst")])
    return u, t_ok, t_off, t_sso


def _wrap(body):
    async def inner(c, ids):
        try:
            await body(c, ids)
        finally:
            await _drop_clients(ids)
    _go(inner)


def test_authorize_unknown_client_no_redirect():
    async def body(c, ids):
        r = await c.get(AUTH, params=_params("nope-client"))
        assert r.status_code == 400 and "location" not in r.headers
    _wrap(body)


def test_authorize_bad_redirect_no_redirect():
    async def body(c, ids):
        cid = await _client(ids)
        for bad in ("http://127.0.0.1:33418/other", "https://evil.example/cb", "http://127.0.0.1:33418/cb"):
            r = await c.get(AUTH, params=_params(cid, redirect_uri=bad))
            assert r.status_code == 400 and "location" not in r.headers, bad
        # Loopback: only the port may differ (RFC 8252 §7.3).
        r = await c.get(AUTH, params=_params(cid, redirect_uri="http://127.0.0.1:5555/cb?keep=1"))
        assert r.status_code == 303
    _wrap(body)


def test_authorize_missing_pkce_redirects_error():
    async def body(c, ids):
        cid = await _client(ids)
        for over in ({"code_challenge": None}, {"code_challenge_method": "plain"},
                     {"code_challenge_method": None}, {"resource": "https://other.example/mcp"}):
            r = await c.get(AUTH, params=_params(cid, **over))
            assert r.status_code == 303, over
            loc = r.headers["location"]
            assert loc.startswith("http://127.0.0.1:33418/cb?")
            q = _q(loc)
            assert q == {"keep": "1", "error": "invalid_request", "state": "st-123"}, (over, q)
        r = await c.get(AUTH, params=_params(cid, scope="mcp admin"))
        assert r.status_code == 303 and _q(r.headers["location"])["error"] == "invalid_scope"
    _wrap(body)


def test_authorize_valid_goes_to_consent():
    async def body(c, ids):
        cid = await _client(ids)
        p = _params(cid, scope="mcp", resource=RESOURCES["mcp"].url)
        r = await c.get(AUTH, params=p)
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith(settings.FRONTEND_URL.rstrip("/") + "/oauth/consent?")
        assert _q(loc) == p
    _wrap(body)


def test_info_lists_enterable_tenants():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, t_off, t_sso = await _setup(ids)
        h = await _session(u, t_ok.id)
        r = await c.get(INFO, params=_params(cid), headers=h)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["client_name"] == "Test Agent" and j["redirect_host"] == "127.0.0.1" and j["unverified"] is True
        assert j["resource"] == RESOURCES["mcp"].url
        assert j["tenants"] == [{"id": t_ok.id, "name": t_ok.name, "role": "analyst"}]
        assert j["sso_tenants"] == [{"id": t_sso.id, "name": t_sso.name, "slug": t_sso.slug}]
        # No session: refused.
        c.cookies.clear()
        assert (await c.get(INFO, params=_params(cid))).status_code == 401
    _wrap(body)


def test_decision_approve_issues_code():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        h = await _session(u, t_ok.id)
        r = await c.post(DECIDE, json={**_params(cid), "tenant_id": t_ok.id, "approve": True}, headers={**h, **XRW})
        assert r.status_code == 200, r.text
        url = r.json()["redirect"]
        assert url.startswith("http://127.0.0.1:33418/cb?")
        q = _q(url)
        assert q["state"] == "st-123" and q["keep"] == "1" and q["code"]
        redis = oauth_routes._oauth_redis()
        key = f"oauth:code:{q['code']}"
        assert redis.keys[key] == 60
        payload = json.loads(redis.vals[key])
        assert payload == {
            "client_id": cid, "redirect_uri": REDIRECT, "code_challenge": CHALLENGE, "user_id": u.id,
            "tenant_id": t_ok.id, "resource": RESOURCES["mcp"].url, "scope": "mcp", "amr": "pwd",
            "sso_tenant_id": None,
        }
    _wrap(body)


def test_decision_rejects_tenant_not_offered():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, t_off, t_sso = await _setup(ids)
        h = await _session(u, t_ok.id)
        for tid in (t_off.id, t_sso.id, 999999999):
            r = await c.post(DECIDE, json={**_params(cid), "tenant_id": tid, "approve": True}, headers={**h, **XRW})
            assert r.status_code == 403, (tid, r.text)
        assert not any(k.startswith("oauth:code:") for k in oauth_routes._oauth_redis().keys)
    _wrap(body)


def test_decision_deny():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        h = await _session(u, t_ok.id)
        r = await c.post(DECIDE, json={**_params(cid), "tenant_id": t_ok.id, "approve": False}, headers={**h, **XRW})
        assert r.status_code == 200, r.text
        assert _q(r.json()["redirect"]) == {"keep": "1", "error": "access_denied", "state": "st-123"}
        assert not any(k.startswith("oauth:code:") for k in oauth_routes._oauth_redis().keys)
    _wrap(body)


def test_decision_requires_csrf_header():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        h = await _session(u, t_ok.id)
        r = await c.post(DECIDE, json={**_params(cid), "tenant_id": t_ok.id, "approve": True}, headers=h)
        assert r.status_code == 403
        assert not any(k.startswith("oauth:code:") for k in oauth_routes._oauth_redis().keys)
    _wrap(body)


def test_info_refuses_bearer_token():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        tok = await make_web_token(None, u, t_ok.id)
        c.cookies.clear()
        r = await c.get(INFO, params=_params(cid), headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401
    _wrap(body)
