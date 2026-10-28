"""The ticketing API never hands exception text to the browser: the SSRF guard's resolved address, a KeyError or
ValueError from settings, only TicketError messages that ticket_sync writes itself (CodeQL py/stack-trace-exposure)."""
import asyncio
import secrets
from types import SimpleNamespace

import httpx
from sqlalchemy import delete

from app.api import deps
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.ticket_connection import TicketConnection
from app.services import ticket_sync
from app.services.ticket_sync import HOST_REFUSED, TicketError


async def _scenario(monkeypatch):
    async with AsyncSessionLocal() as db:
        t = Tenant(name="tk-err", slug=f"tk-err-{secrets.token_hex(3)}")
        db.add(t)
        await db.flush()
        db.add(TicketConnection(tenant_id=t.id, provider="jira", base_url="https://10.0.0.5",
                                credentials_enc=ticket_sync.encrypt_credentials({"email": "e", "token": "t"}),
                                settings={"project_key": "SEC"}))
        await db.commit()
        tid = t.id
    admin = SimpleNamespace(id=None, is_super_admin=False, is_active=True, _active_tenant_id=tid,
                            memberships=[SimpleNamespace(tenant_id=tid, role="admin")])
    app.dependency_overrides[deps.get_current_active_user] = lambda: admin
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            test = lambda: c.post("/api/v1/ticketing/connection/test")  # noqa: E731
            r = (await test()).json()
            assert r == {"ok": False, "error": HOST_REFUSED} and "10.0.0.5" not in r["error"], r

            real_client = ticket_sync.client
            for exc in (KeyError("token-SECRET"), ValueError("decrypt failed: SECRET")):
                def broken(*a, exc=exc, **k):
                    raise exc
                monkeypatch.setattr(ticket_sync, "client", broken)
                r = (await test()).json()
                assert r["ok"] is False and "SECRET" not in r["error"] and r["error"].startswith("Check your settings"), r

            class Fails:
                async def check(self):
                    raise TicketError("jira returned HTTP 401 for GET /rest/api/3/myself")
            monkeypatch.setattr(ticket_sync, "client", lambda *a, **k: Fails())
            assert (await test()).json()["error"] == "jira returned HTTP 401 for GET /rest/api/3/myself"
            monkeypatch.setattr(ticket_sync, "client", real_client)
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(TicketConnection).where(TicketConnection.tenant_id == tid))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()


def test_ticketing_errors_never_echo_exception_text(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
