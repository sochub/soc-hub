"""Tenant mailbox admin API: CRUD, password handling, tenant isolation, connection test."""
import asyncio
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.tenant_mailbox import TenantMailbox
from app.models.user import User
from app.utils.crypto import decrypt

PW = "S3cr3t-Pass-xyz"


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


def _body(**kw):
    b = {"name": "Phishing", "host": "imap.example.test", "username": "soc@example.test", "password": PW}
    b.update(kw)
    return b


async def _scenario(body):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": []}
    state = {"role": "admin", "tenant": None, "user": None}
    async with AsyncSessionLocal() as db:
        try:
            t1, t2 = (Tenant(name="mb-test", slug=f"mb-{h}-{i}") for i in (1, 2))
            u = User(email=f"mb-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([t1, t2, u])
            await db.flush()
            ids["tenants"] += [int(t1.id), int(t2.id)]
            ids["users"].append(int(u.id))
            await db.commit()
            state["tenant"], state["user"] = ids["tenants"][0], u
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_admin] = lambda: state["user"] if state["role"] == "admin" else _deny()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                await body(client, state, ids["tenants"])
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as d:
                await d.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
                await d.execute(delete(TenantMailbox).where(TenantMailbox.tenant_id.in_(ids["tenants"])))
                await d.execute(delete(User).where(User.id.in_(ids["users"])))
                await d.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await d.commit()


async def _row(mid):
    async with AsyncSessionLocal() as d:
        return (await d.execute(select(TenantMailbox).where(TenantMailbox.id == mid))).scalar_one_or_none()


def test_crud_and_encryption():
    async def body(c, s, tids):
        r = await c.post("/api/v1/mailboxes/", json=_body())
        assert r.status_code in (200, 201), r.text
        out = r.json()
        assert out["password_set"] is True and out["poll_interval_seconds"] == 120
        assert PW not in r.text and "password_enc" not in r.text
        mid = out["id"]
        row = await _row(mid)
        assert row.password_enc != PW and decrypt(row.password_enc) == PW
        old_enc = row.password_enc

        lst = await c.get("/api/v1/mailboxes/")
        assert [m["id"] for m in lst.json()] == [mid] and PW not in lst.text

        # PUT without password keeps the old one
        r = await c.put(f"/api/v1/mailboxes/{mid}", json={"name": "Renamed", "host": "h2.example.test",
                                                         "username": "soc@example.test"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Renamed" and r.json()["password_set"] is True
        assert (await _row(mid)).password_enc == old_enc
        # PUT with password replaces it
        r = await c.put(f"/api/v1/mailboxes/{mid}", json=_body(password="new-pass-1"))
        assert decrypt((await _row(mid)).password_enc) == "new-pass-1"

        assert (await c.delete(f"/api/v1/mailboxes/{mid}")).status_code == 204
        assert await _row(mid) is None
        async with AsyncSessionLocal() as d:
            logs = (await d.execute(select(AuditLog).where(AuditLog.entity_type == "mailbox",
                                                           AuditLog.tenant_id == tids[0]))).scalars().all()
        assert {"created", "updated", "deleted"} <= {a.action for a in logs}
        assert PW not in str([a.changes for a in logs])
    asyncio.run(_scenario(body))


def test_analyst_forbidden():
    async def body(c, s, tids):
        s["role"] = "analyst"
        assert (await c.get("/api/v1/mailboxes/")).status_code == 403
        assert (await c.post("/api/v1/mailboxes/", json=_body())).status_code == 403
    asyncio.run(_scenario(body))


def test_interval_validation_and_create_needs_password():
    async def body(c, s, tids):
        assert (await c.post("/api/v1/mailboxes/", json=_body(poll_interval_seconds=30))).status_code == 422
        b = _body()
        b.pop("password")
        assert (await c.post("/api/v1/mailboxes/", json=b)).status_code == 422
    asyncio.run(_scenario(body))


def test_cross_tenant_404():
    async def body(c, s, tids):
        mid = (await c.post("/api/v1/mailboxes/", json=_body())).json()["id"]
        s["tenant"] = tids[1]
        assert (await c.get("/api/v1/mailboxes/")).json() == []
        assert (await c.put(f"/api/v1/mailboxes/{mid}", json=_body())).status_code == 404
        assert (await c.delete(f"/api/v1/mailboxes/{mid}")).status_code == 404
        assert (await c.post(f"/api/v1/mailboxes/{mid}/test")).status_code == 404
        assert await _row(mid) is not None
    asyncio.run(_scenario(body))


def test_connection_endpoint(monkeypatch):
    from app.services import email_intake
    seen = {}

    def fake(mb):
        seen["host"] = mb.host
        return {"ok": True, "message_count": 7, "error": None}
    monkeypatch.setattr(email_intake, "test_connection", fake)

    async def body(c, s, tids):
        mid = (await c.post("/api/v1/mailboxes/", json=_body())).json()["id"]
        r = await c.post(f"/api/v1/mailboxes/{mid}/test")
        assert r.status_code == 200 and r.json() == {"ok": True, "message_count": 7, "error": None}
        assert seen["host"] == "imap.example.test"
    asyncio.run(_scenario(body))


def test_test_connection_scrubs_password(monkeypatch):
    import imaplib
    from app.services import email_intake
    from app.utils.crypto import encrypt

    class Boom:
        def __init__(self, *a, **k):
            raise OSError(f"login failed for {PW}")
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Boom)
    mb = TenantMailbox(host="h", port=993, use_ssl=True, username="u", password_enc=encrypt(PW), folder="INBOX")
    res = email_intake.test_connection(mb)
    assert res["ok"] is False and PW not in res["error"] and res["message_count"] is None
