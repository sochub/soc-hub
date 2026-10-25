"""MCP server over Streamable HTTP: OAuth discovery, auth, role checks, via attribution, one audit row per call."""
import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, select, update

from app.auth import sessions
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.mcp import tools
from app.mcp.server import mcp_server
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.case import TimelineEvent
from app.models.membership import TenantMembership
from app.models.oauth_client import OAuthClient
from app.models.playbook import PlaybookTemplate
from app.oauth.resources import RESOURCES, issuer
from app.oauth.tokens import issue_oauth_access_token
from tests.guest_fixtures import Ids, cleanup, make_case, make_tenant, make_user
from tests.session_fixtures import make_web_token

BASE = issuer()  # requests carry the public Host, which the transport security allowlist accepts
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
CLIENT_NAME = "Test MCP Client"


def _go(body):
    async def main():
        await engine.dispose()
        ids = Ids()
        ids.clients, ids.playbooks = [], []
        # StreamableHTTPSessionManager.run() is single-use per instance (stateless or not); each test runs the
        # app lifespan in its own loop.
        mcp_server.session_manager._has_started = False
        try:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
                    await body(c, ids)
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(PlaybookTemplate).where(PlaybookTemplate.id.in_(ids.playbooks or [-1])))
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(ids.users or [-1])))
                await db.execute(delete(AuthSession).where(AuthSession.user_id.in_(ids.users or [-1])))
                await db.execute(delete(OAuthClient).where(OAuthClient.id.in_(ids.clients or ["-"])))
                await db.commit()
            await cleanup(ids)
            await engine.dispose()
    asyncio.run(main())


async def _setup(ids, role="analyst"):
    """Tenant (MCP on), member with ``role``, an OAuth client and an MCP access token."""
    async with AsyncSessionLocal() as db:
        t = await make_tenant(db, ids)
        t.mcp_enabled = True
        user = await make_user(db, ids)
        db.add(TenantMembership(user_id=user.id, tenant_id=t.id, role=role))
        cid = f"mcp-test-{ids.h}-{len(ids.clients)}"
        db.add(OAuthClient(id=cid, client_name=CLIENT_NAME, redirect_uris=["http://127.0.0.1:1/cb"],
                           created_ip="1.1.1.1"))
        await db.commit()
        ids.clients.append(cid)
        row, _ = await sessions.create_session(db, user, kind="oauth", amr="pwd", tenant_id=t.id, client_id=cid,
                                               resource=RESOURCES["mcp"].url, scope="mcp")
        await db.commit()
        token = issue_oauth_access_token(row, user)
        return t, user, row.id, token


def _parse(r: httpx.Response) -> dict:
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        msgs = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:") and line[5:].strip()]
        return [m for m in msgs if "id" in m][-1]
    return r.json()


class Mcp:
    def __init__(self, c: httpx.AsyncClient, token: str):
        self.c, self.token, self.ready, self.n = c, token, False, 0

    def _h(self):
        h = {**ACCEPT, "Authorization": f"Bearer {self.token}"}
        if self.ready:  # stateless server: no Mcp-Session-Id, only the negotiated protocol version
            h["Mcp-Protocol-Version"] = "2025-11-25"
        return h

    async def post(self, payload: dict) -> httpx.Response:
        return await self.c.post("/mcp", json=payload, headers=self._h())

    async def rpc(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        r = await self.post({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        assert r.status_code == 200, (r.status_code, r.text)
        return _parse(r)

    async def init(self):
        self.n += 1
        r = await self.post({"jsonrpc": "2.0", "id": self.n, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"}}})
        assert r.status_code == 200, r.text
        assert "mcp-session-id" not in r.headers  # stateless transport
        self.ready = True
        r = await self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert r.status_code == 202, r.text
        return self

    async def call(self, tool: str, /, **args) -> dict:
        res = (await self.rpc("tools/call", {"name": tool, "arguments": args}))["result"]
        text = res["content"][0]["text"] if res.get("content") else ""
        return {"error": bool(res.get("isError")), "text": text,
                "data": None if res.get("isError") else json.loads(text)}


def test_unauthenticated_401_points_to_metadata():
    async def body(c, ids):
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                         headers=ACCEPT)
        assert r.status_code == 401
        www = r.headers["www-authenticate"]
        assert www.startswith("Bearer ") and 'resource_metadata="' in www
        url = www.split('resource_metadata="', 1)[1].split('"', 1)[0]
        parsed = urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}" == BASE
        m = await c.get(parsed.path)
        assert m.status_code == 200, m.text
        j = m.json()
        assert j["resource"] == RESOURCES["mcp"].url
        assert j["authorization_servers"] == [issuer()]
        assert j["scopes_supported"] == ["mcp"]
        # The bare well-known path answers too (clients that try it first).
        assert (await c.get("/.well-known/oauth-protected-resource")).json()["resource"] == RESOURCES["mcp"].url
    _go(body)


def test_tools_list_and_whoami():
    async def body(c, ids):
        t, user, _, token = await _setup(ids, role="analyst")
        s = await Mcp(c, token).init()
        listed = (await s.rpc("tools/list"))["result"]["tools"]
        names = {x["name"] for x in listed}
        assert names == set(tools.MIN_ROLE)
        note = next(x for x in listed if x["name"] == "add_timeline_note")
        assert set(note["inputSchema"]["required"]) == {"case_id", "content"}
        assert "db" not in note["inputSchema"]["properties"] and "ctx" not in note["inputSchema"]["properties"]
        assert next(x for x in listed if x["name"] == "get_case")["annotations"]["readOnlyHint"] is True
        r = await s.call("whoami")
        assert not r["error"], r["text"]
        assert r["data"]["tenant"]["id"] == t.id and r["data"]["role"] == "analyst"
        assert r["data"]["email"] == user.email and r["data"]["client"] == CLIENT_NAME
    _go(body)


def test_viewer_cannot_write():
    async def body(c, ids):
        t, user, _, token = await _setup(ids, role="viewer")
        async with AsyncSessionLocal() as db:
            case = await make_case(db, ids, t)
        s = await Mcp(c, token).init()
        r = await s.call("add_timeline_note", case_id=case.id, content="should not land")
        assert r["error"] and "analyst role" in r["text"]
        async with AsyncSessionLocal() as db:
            n = (await db.execute(select(TimelineEvent).where(TimelineEvent.case_id == case.id))).scalars().all()
        assert n == []
        assert not (await s.call("get_case", case_id=case.id))["error"]
    _go(body)


def test_admin_only_draft():
    async def body(c, ids):
        _, _, _, analyst_token = await _setup(ids, role="analyst")
        _, _, _, admin_token = await _setup(ids, role="admin")
        tasks = [{"phase": "identification", "title": "Scope it"}]
        r = await (await Mcp(c, analyst_token).init()).call("create_playbook_draft", name="PB", tasks=tasks)
        assert r["error"] and "admin role" in r["text"]
        r = await (await Mcp(c, admin_token).init()).call("create_playbook_draft", name="PB", tasks=tasks)
        assert not r["error"], r["text"]
        ids.playbooks.append(r["data"]["id"])
        assert r["data"]["status"] == "draft"
    _go(body)


def test_revoked_mid_session():
    async def body(c, ids):
        _, _, sid, token = await _setup(ids)
        s = await Mcp(c, token).init()
        assert not (await s.call("whoami"))["error"]
        async with AsyncSessionLocal() as db:
            await db.execute(update(AuthSession).where(AuthSession.id == sid)
                             .values(revoked_at=datetime.now(timezone.utc)))
            await db.commit()
        r = await s.post({"jsonrpc": "2.0", "id": 99, "method": "tools/call",
                          "params": {"name": "whoami", "arguments": {}}})
        assert r.status_code == 401
    _go(body)


def test_write_is_attributed():
    async def body(c, ids):
        t, user, _, token = await _setup(ids)
        async with AsyncSessionLocal() as db:
            case = await make_case(db, ids, t)
        s = await Mcp(c, token).init()
        r = await s.call("add_timeline_note", case_id=case.id, content="seen beaconing via MCP")
        assert not r["error"], r["text"]
        async with AsyncSessionLocal() as db:
            ev = (await db.execute(select(TimelineEvent).where(TimelineEvent.case_id == case.id))).scalars().one()
            assert ev.via == "mcp" and ev.via_client == CLIENT_NAME
            rows = (await db.execute(select(AuditLog).where(AuditLog.action == "mcp.add_timeline_note",
                                                            AuditLog.user_id == user.id))).scalars().all()
            assert len(rows) == 1
            row = rows[0]
            assert row.category == "write" and row.actor == f"mcp:{CLIENT_NAME}" and row.tenant_id == t.id
            assert row.changes["client_id"] == ids.clients[0]
            assert row.changes["args"] == {"case_id": case.id, "content": "seen beaconing via MCP"}
            same_request = (await db.execute(select(AuditLog).where(
                AuditLog.request_id == row.request_id))).scalars().all()
            assert [x.id for x in same_request] == [row.id]  # no second, unattributed row for that request
            reads = (await db.execute(select(AuditLog).where(AuditLog.action == "mcp.whoami",
                                                             AuditLog.user_id == user.id))).scalars().all()
            assert reads == []
        r = await s.call("whoami")
        async with AsyncSessionLocal() as db:
            reads = (await db.execute(select(AuditLog).where(AuditLog.action == "mcp.whoami",
                                                             AuditLog.user_id == user.id))).scalars().all()
            assert len(reads) == 1 and reads[0].category == "read" and reads[0].tenant_id == t.id
            # Protocol requests (initialize, tools/list) are attributed too, not anonymous.
            anon = (await db.execute(select(AuditLog).where(AuditLog.route.like("/mcp%"),
                                                            AuditLog.tenant_id == t.id,
                                                            AuditLog.user_id.is_(None)))).scalars().all()
            assert anon == []
            proto = (await db.execute(select(AuditLog).where(AuditLog.user_id == user.id,
                                                             AuditLog.action == "POST /mcp"))).scalars().all()
            assert len(proto) >= 2 and all(x.actor == f"mcp:{CLIENT_NAME}" and x.tenant_id == t.id for x in proto)
            assert row.route == "/mcp" and row.method == "POST"
    _go(body)


def test_web_token_rejected():
    async def body(c, ids):
        t, user, _, _ = await _setup(ids)
        web = await make_web_token(None, user, t.id)
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                         headers={**ACCEPT, "Authorization": f"Bearer {web}"})
        assert r.status_code == 401
    _go(body)
