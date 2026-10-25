import asyncio

import httpx
from sqlalchemy import delete, select

from app.auth import sessions
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.oauth_client import OAuthClient
from app.models.tenant import Tenant
from tests.session_fixtures import make_web_token
from tests.test_mfa_api import _cleanup, _hdr, _setup, _user

BASE = "/api/v1"


def _scenario(body):
    async def go():
        await engine.dispose()
        ids = await _setup()
        extra = {"clients": [], "sessions": []}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await body(c, ids, extra)
        finally:
            async with AsyncSessionLocal() as db:
                if extra["sessions"]:
                    await db.execute(delete(AuthSession).where(AuthSession.id.in_(extra["sessions"])))
                if extra["clients"]:
                    await db.execute(delete(OAuthClient).where(OAuthClient.id.in_(extra["clients"])))
                await db.commit()
            await _cleanup(ids)
            await engine.dispose()
    asyncio.run(go())


async def _oauth(ids, extra, who, tenant_key="ta"):
    cid = f"sess-test-{ids[who]}-{len(extra['clients'])}"
    async with AsyncSessionLocal() as db:
        db.add(OAuthClient(id=cid, client_name="Test Agent", redirect_uris=["http://127.0.0.1/cb"]))
        await db.flush()
        u = await _user(ids[who])
        row, _ = await sessions.create_session(db, u, kind="oauth", amr="pwd", tenant_id=ids[tenant_key],
                                               client_id=cid)
        await db.commit()
        extra["clients"].append(cid)
        extra["sessions"].append(row.id)
        return row.id


def test_list_own_sessions_marks_current():
    async def body(c, ids, extra):
        hdr = await _hdr(ids["member"], ids["ta"])
        oid = await _oauth(ids, extra, "member")
        r = await c.get(f"{BASE}/users/me/sessions", headers=hdr)
        assert r.status_code == 200, r.text
        rows = r.json()
        assert "refresh_hash" not in r.text
        assert sum(1 for x in rows if x["current"]) == 1
        assert next(x for x in rows if x["id"] == oid)["client_name"] == "Test Agent"
        assert next(x for x in rows if x["current"])["kind"] == "web"
    _scenario(body)


def test_revoke_own_session():
    async def body(c, ids, extra):
        hdr = await _hdr(ids["member"], ids["ta"])
        oid = await _oauth(ids, extra, "member")
        assert (await c.delete(f"{BASE}/users/me/sessions/{oid}", headers=hdr)).status_code == 204
        assert oid not in [x["id"] for x in (await c.get(f"{BASE}/users/me/sessions", headers=hdr)).json()]
        assert (await c.delete(f"{BASE}/users/me/sessions/{oid}", headers=hdr)).status_code == 404
    _scenario(body)


def test_cannot_revoke_others():
    async def body(c, ids, extra):
        hdr = await _hdr(ids["analyst"], ids["ta"])
        oid = await _oauth(ids, extra, "member")
        assert (await c.delete(f"{BASE}/users/me/sessions/{oid}", headers=hdr)).status_code == 404
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AuthSession).where(AuthSession.id == oid))).scalar_one()
            assert row.revoked_at is None
    _scenario(body)


def test_admin_lists_and_revokes_tenant_oauth():
    async def body(c, ids, extra):
        adm = await _hdr(ids["admin"], ids["ta"])
        oid = await _oauth(ids, extra, "member")
        other = await _oauth(ids, extra, "admin_b", "tb")
        r = await c.get(f"{BASE}/tenants/current/oauth-sessions", headers=adm)
        assert r.status_code == 200, r.text
        got = {x["id"]: x for x in r.json()}
        assert oid in got and other not in got
        assert got[oid]["user_email"].endswith("-member@example.com")
        assert (await c.delete(f"{BASE}/tenants/current/oauth-sessions/{other}", headers=adm)).status_code == 404
        # a web session is not revocable through the admin route
        async with AsyncSessionLocal() as db:
            web = (await db.execute(select(AuthSession).where(AuthSession.user_id == ids["admin"]))).scalars().first()
        assert (await c.delete(f"{BASE}/tenants/current/oauth-sessions/{web.id}", headers=adm)).status_code == 404
        assert (await c.delete(f"{BASE}/tenants/current/oauth-sessions/{oid}", headers=adm)).status_code == 204
        assert oid not in [x["id"] for x in (await c.get(f"{BASE}/tenants/current/oauth-sessions", headers=adm)).json()]
    _scenario(body)


def test_analyst_cannot_list_tenant_oauth():
    async def body(c, ids, extra):
        ana = await _hdr(ids["analyst"], ids["ta"])
        assert (await c.get(f"{BASE}/tenants/current/oauth-sessions", headers=ana)).status_code == 403
        oid = await _oauth(ids, extra, "member")
        assert (await c.delete(f"{BASE}/tenants/current/oauth-sessions/{oid}", headers=ana)).status_code == 403
    _scenario(body)


def test_mcp_toggle_admin_only():
    async def body(c, ids, extra):
        adm = await _hdr(ids["admin"], ids["ta"])
        ana = await _hdr(ids["analyst"], ids["ta"])
        assert (await c.get(f"{BASE}/tenants/current/security", headers=adm)).json()["mcp_enabled"] is False
        assert (await c.put(f"{BASE}/tenants/current/security", headers=ana, json={"mcp_enabled": True})).status_code == 403
        r = await c.put(f"{BASE}/tenants/current/security", headers=adm, json={"mcp_enabled": True})
        assert r.status_code == 200 and r.json()["mcp_enabled"] is True and r.json()["require_mfa"] is False
        async with AsyncSessionLocal() as db:
            t = (await db.execute(select(Tenant).where(Tenant.id == ids["ta"]))).scalar_one()
            assert t.mcp_enabled is True
            n = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == ids["ta"],
                                                         AuditLog.action == "tenant.mcp_toggled"))).scalars().all()
            assert len(n) == 1
    _scenario(body)
