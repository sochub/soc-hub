"""POST /alerts/webhook is idempotent on (tenant, source, external_id)."""
import asyncio
import secrets

import httpx
from sqlalchemy import delete, func, select

from app.api.v1 import alerts as alerts_api
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.case import Alert
from app.models.tenant import Tenant
from app.models.webhook import Webhook

URL = "/api/v1/alerts/webhook"


async def _scenario(emitted, concurrent):
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t = Tenant(name="idem-test", slug=f"idem-test-{h}")
        db.add(t)
        await db.flush()
        wh = Webhook(tenant_id=t.id, name="Splunk", api_key=f"idem-{secrets.token_hex(16)}")
        db.add(wh)
        await db.commit()
        tid, wid, key = t.id, wh.id, wh.api_key
    try:
        body = {"external_id": f"ext-{h}", "title": "dup test", "payload": {"n": 1}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            post = lambda: c.post(URL, json=body, headers={"X-API-Key": key})  # noqa: E731
            if concurrent:
                r1, r2 = await asyncio.gather(post(), post())
            else:
                r1, r2 = await post(), await post()
        assert (r1.status_code, r2.status_code) == (200, 200), (r1.text, r2.text)
        assert r1.json()["id"] == r2.json()["id"]
        assert emitted == [r1.json()["id"]]
        async with AsyncSessionLocal() as db:
            n = (await db.execute(select(func.count()).select_from(Alert).where(Alert.tenant_id == tid))).scalar()
        assert n == 1
    finally:
        async with AsyncSessionLocal() as db:
            ids = (await db.execute(select(Alert.id).where(Alert.tenant_id == tid))).scalars().all()
            await db.execute(delete(Alert).where(Alert.id.in_(ids)))
            await db.execute(delete(Webhook).where(Webhook.id == wid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()


def _run(monkeypatch, concurrent):
    emitted = []
    monkeypatch.setattr(alerts_api, "emit_event", lambda tenant_id, ev, **kw: emitted.append(kw["alert_id"]))
    asyncio.run(_scenario(emitted, concurrent))


def test_duplicate_external_id_returns_same_alert_and_emits_once(monkeypatch):
    _run(monkeypatch, concurrent=False)


def test_concurrent_duplicates_create_one_row(monkeypatch):
    _run(monkeypatch, concurrent=True)
