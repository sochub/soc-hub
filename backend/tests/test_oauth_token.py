"""OAuth token endpoint (code exchange + refresh), revocation and access-token verification."""
import base64
import hashlib
import secrets

import jwt
from sqlalchemy import select, update

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app  # noqa: F401
from app.models.auth_session import AuthSession
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.oauth import tokens
from app.oauth.resources import RESOURCES, issuer
from tests.session_fixtures import make_web_token
from tests.test_oauth_authorize import DECIDE, REDIRECT, XRW, _client, _params, _q, _session, _setup, _wrap

TOKEN = "/api/v1/oauth/token"
REVOKE = "/api/v1/oauth/revoke"


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _code(c, cid, user, tenant_id):
    verifier, challenge = _pkce()
    h = await _session(user, tenant_id)
    r = await c.post(DECIDE, json={**_params(cid, code_challenge=challenge), "tenant_id": tenant_id, "approve": True},
                     headers={**h, **XRW})
    assert r.status_code == 200, r.text
    c.cookies.clear()
    return _q(r.json()["redirect"])["code"], verifier


def _exchange_form(cid, code, verifier, **over):
    f = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": cid,
         "code_verifier": verifier}
    f.update(over)
    return {k: v for k, v in f.items() if v is not None}


async def _grant(c, ids):
    """User (analyst in t_ok, mcp on) + client + a completed code exchange."""
    cid = await _client(ids)
    u, t_ok, t_off, t_sso = await _setup(ids)
    code, verifier = await _code(c, cid, u, t_ok.id)
    r = await c.post(TOKEN, data=_exchange_form(cid, code, verifier))
    assert r.status_code == 200, r.text
    return cid, u, t_ok, r.json()


async def _verify(tok):
    async with AsyncSessionLocal() as db:
        return await tokens.verify_oauth_access_token(db, tok, resource="mcp")


async def _sid(tok):
    return jwt.decode(tok, options={"verify_signature": False})["sid"]


async def _set_tenant(tid, **vals):
    async with AsyncSessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == tid).values(**vals))
        await db.commit()


def test_code_exchange_ok():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        assert j["token_type"] == "Bearer" and j["expires_in"] == 3600 and j["scope"] == "mcp"
        assert j["refresh_token"] and j["access_token"]
        claims = jwt.decode(j["access_token"], settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
                            audience=RESOURCES["mcp"].url, issuer=issuer())
        assert claims["sub"] == u.email and claims["tid"] == t_ok.id and claims["cid"] == cid
        assert claims["purpose"] == "oauth" and claims["scope"] == "mcp" and claims["exp"] - claims["iat"] == 3600
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AuthSession).where(AuthSession.id == claims["sid"]))).scalar_one()
        assert row.kind == "oauth" and row.client_id == cid and row.tenant_id == t_ok.id and row.amr == "pwd"
        assert row.resource == RESOURCES["mcp"].url and row.scope == "mcp"
    _wrap(body)


def test_token_response_headers_and_errors():
    async def body(c, ids):
        r = await c.post(TOKEN, data={"grant_type": "password"})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_grant_type"
        assert r.headers["cache-control"] == "no-store" and r.headers["pragma"] == "no-cache"
        r = await c.post(TOKEN, data={})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        r = await c.post(TOKEN, data={"grant_type": "authorization_code", "client_id": "x"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
    _wrap(body)


def test_code_single_use():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        code, verifier = await _code(c, cid, u, t_ok.id)
        assert (await c.post(TOKEN, data=_exchange_form(cid, code, verifier))).status_code == 200
        r = await c.post(TOKEN, data=_exchange_form(cid, code, verifier))
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    _wrap(body)


def test_wrong_verifier():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        code, _verifier = await _code(c, cid, u, t_ok.id)
        r = await c.post(TOKEN, data=_exchange_form(cid, code, secrets.token_urlsafe(48)))
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        # A failed attempt burns the code.
        code2, verifier2 = await _code(c, cid, u, t_ok.id)
        assert (await c.post(TOKEN, data=_exchange_form(cid, code2, "short"))).json()["error"] == "invalid_grant"
        r = await c.post(TOKEN, data=_exchange_form(cid, code2, verifier2))
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    _wrap(body)


def test_redirect_mismatch():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        code, verifier = await _code(c, cid, u, t_ok.id)
        r = await c.post(TOKEN, data=_exchange_form(cid, code, verifier,
                                                    redirect_uri="http://127.0.0.1:5555/cb?keep=1"))
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    _wrap(body)


def test_client_mismatch():
    async def body(c, ids):
        cid = await _client(ids)
        other = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        code, verifier = await _code(c, cid, u, t_ok.id)
        r = await c.post(TOKEN, data=_exchange_form(other, code, verifier))
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    _wrap(body)


def test_exchange_refused_when_tenant_gate_closed():
    async def body(c, ids):
        cid = await _client(ids)
        u, t_ok, _, _ = await _setup(ids)
        code, verifier = await _code(c, cid, u, t_ok.id)
        await _set_tenant(t_ok.id, mcp_enabled=False)
        r = await c.post(TOKEN, data=_exchange_form(cid, code, verifier))
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    _wrap(body)


def test_refresh_rotation_oauth():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        r = await c.post(TOKEN, data={"grant_type": "refresh_token", "refresh_token": j["refresh_token"],
                                      "client_id": cid})
        assert r.status_code == 200, r.text
        j2 = r.json()
        assert j2["refresh_token"] != j["refresh_token"] and j2["access_token"]
        assert r.headers["cache-control"] == "no-store"
        assert (await _verify(j2["access_token"])) is not None
        # Wrong client: refused, session untouched.
        r = await c.post(TOKEN, data={"grant_type": "refresh_token", "refresh_token": j2["refresh_token"],
                                      "client_id": await _client(ids)})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        r = await c.post(TOKEN, data={"grant_type": "refresh_token", "refresh_token": j2["refresh_token"],
                                      "client_id": cid})
        assert r.status_code == 200, r.text
        # A web refresh token is not accepted here.
        r = await c.post(TOKEN, data={"grant_type": "refresh_token", "refresh_token": "0" * 32 + ".x",
                                      "client_id": cid})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    _wrap(body)


def test_refresh_after_mcp_disabled_invalid_grant():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        await _set_tenant(t_ok.id, mcp_enabled=False)
        r = await c.post(TOKEN, data={"grant_type": "refresh_token", "refresh_token": j["refresh_token"],
                                      "client_id": cid})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AuthSession).where(
                AuthSession.id == await _sid(j["access_token"])))).scalar_one()
        assert row.revoked_at is not None
    _wrap(body)


def test_revoke():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        sid = await _sid(j["access_token"])
        # Unknown token, wrong client, wrong secret: 200 and nothing revoked.
        for form in ({"token": "garbage", "client_id": cid},
                     {"token": j["refresh_token"], "client_id": await _client(ids)},
                     {"token": f"{sid}.wrong", "client_id": cid},
                     {}):
            r = await c.post(REVOKE, data=form)
            assert r.status_code == 200, form
        assert (await _verify(j["access_token"])) is not None
        r = await c.post(REVOKE, data={"token": j["refresh_token"], "client_id": cid})
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert (await _verify(j["access_token"])) is None
        r = await c.post(TOKEN, data={"grant_type": "refresh_token", "refresh_token": j["refresh_token"],
                                      "client_id": cid})
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    _wrap(body)


def test_verify_ok_returns_live_role():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        p = await _verify(j["access_token"])
        assert p is not None and p.role == "analyst" and p.tenant_id == t_ok.id and p.user.id == u.id
        assert p.client_id == cid and p.client_name == "Test Agent" and p.scopes == ("mcp",)
        assert p.session_id == await _sid(j["access_token"])
        async with AsyncSessionLocal() as db:
            await db.execute(update(TenantMembership).where(
                TenantMembership.user_id == u.id, TenantMembership.tenant_id == t_ok.id).values(role="viewer"))
            await db.commit()
        p = await _verify(j["access_token"])
        assert p is not None and p.role == "viewer"
    _wrap(body)


def test_verify_rejects_web_token():
    async def body(c, ids):
        u, t_ok, _, _ = await _setup(ids)
        web = await make_web_token(None, u, t_ok.id)
        assert (await _verify(web)) is None
        assert (await _verify("not-a-jwt")) is None
        async with AsyncSessionLocal() as db:
            assert (await tokens.verify_oauth_access_token(db, web, resource="nope")) is None
    _wrap(body)


def test_verify_rejects_other_audience_and_tampering():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        claims = jwt.decode(j["access_token"], options={"verify_signature": False})
        for over in ({"aud": "https://other.example/mcp"}, {"iss": "https://evil.example"}, {"tv": 99},
                     {"tid": t_ok.id + 100000}, {"purpose": None}, {"cid": "other"}):
            forged = jwt.encode({**claims, **over}, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
            assert (await _verify(forged)) is None, over
        bad_sig = jwt.encode(claims, "x" * 64, algorithm=settings.ALGORITHM)
        assert (await _verify(bad_sig)) is None
    _wrap(body)


def test_web_api_rejects_oauth_token():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        r = await c.get("/api/v1/users/me", headers={"Authorization": f"Bearer {j['access_token']}"})
        assert r.status_code == 401
    _wrap(body)


def test_verify_after_membership_removed_none():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        async with AsyncSessionLocal() as db:
            await db.execute(TenantMembership.__table__.delete().where(
                TenantMembership.user_id == u.id, TenantMembership.tenant_id == t_ok.id))
            await db.commit()
        assert (await _verify(j["access_token"])) is None
    _wrap(body)


def test_verify_after_session_revoked_none():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        from app.auth import sessions
        async with AsyncSessionLocal() as db:
            assert await sessions.revoke(db, await _sid(j["access_token"]), user_id=u.id)
            await db.commit()
        assert (await _verify(j["access_token"])) is None
    _wrap(body)


def test_verify_after_mcp_disabled_none():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        await _set_tenant(t_ok.id, mcp_enabled=False)
        assert (await _verify(j["access_token"])) is None
    _wrap(body)


def test_verify_after_login_method_change_none():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        await _set_tenant(t_ok.id, member_login_method="oidc")
        assert (await _verify(j["access_token"])) is None
    _wrap(body)


def test_verify_super_admin_needs_membership():
    async def body(c, ids):
        cid, u, t_ok, j = await _grant(c, ids)
        from app.models.user import User
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == u.id).values(is_super_admin=True))
            await db.commit()
        p = await _verify(j["access_token"])
        assert p is not None and p.role == "analyst"  # the membership role, not a blanket admin
        async with AsyncSessionLocal() as db:
            await db.execute(TenantMembership.__table__.delete().where(
                TenantMembership.user_id == u.id, TenantMembership.tenant_id == t_ok.id))
            await db.commit()
        assert (await _verify(j["access_token"])) is None
    _wrap(body)
