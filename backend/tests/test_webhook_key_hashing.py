"""Webhook keys: Argon2 at rest; pre-Argon2 (SHA-256) rows are rejected until rotated."""
import asyncio
import hashlib
import secrets

import httpx
from sqlalchemy import delete

from app.api import deps
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.case import Alert
from app.models.tenant import Tenant
from app.models.webhook import Webhook, hash_webhook_key, webhook_key_prefix


async def _scenario():
    h = secrets.token_hex(3)
    old_key, key = f"whk_{secrets.token_urlsafe(32)}", f"whk_{secrets.token_urlsafe(32)}"
    async with AsyncSessionLocal() as db:
        t = Tenant(name="whk-test", slug=f"whk-test-{h}")
        db.add(t)
        await db.flush()
        legacy = Webhook(tenant_id=t.id, name="Legacy", key_prefix=old_key[:8],
                         key_hash=hashlib.sha256(old_key.encode()).hexdigest())  # as stored before Argon2
        current = Webhook(tenant_id=t.id, name="Current", key_prefix=webhook_key_prefix(key),
                          key_hash=hash_webhook_key(key))
        db.add_all([legacy, current])
        await db.commit()
        tid, lid, cid = t.id, legacy.id, current.id
    try:
        assert legacy.needs_rotation and not current.needs_rotation
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            async def post(k, n):
                return await c.post("/api/v1/alerts/webhook", headers={"X-API-Key": k},
                                    json={"external_id": f"{h}-{n}", "title": "t", "payload": {}})
            assert (await post(old_key, 0)).status_code == 401  # legacy SHA-256 keys no longer authenticate
            deps._verified_webhooks.clear()
            assert (await post(key, 1)).status_code == 200  # Argon2 verify
            assert (await post(key, 2)).status_code == 200  # cached
            assert (await post(key[:-1] + ("A" if key[-1] != "A" else "B"), 3)).status_code == 401
            async with AsyncSessionLocal() as db:  # a deleted webhook's cached key stops working
                await db.execute(delete(Webhook).where(Webhook.id == cid))
                await db.commit()
            assert (await post(key, 4)).status_code == 401
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Alert).where(Alert.tenant_id == tid))
            await db.execute(delete(Webhook).where(Webhook.id.in_([lid, cid])))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()


def test_legacy_rejected_argon2_verified_and_cache_respects_deletion():
    asyncio.run(_scenario())


def test_rotate_flags_and_replaces_key():
    from tests.audit_fixtures import hdr, run

    async def body(c, w):
        admin = hdr(w, "admin")
        old_key = f"whk_{secrets.token_urlsafe(32)}"
        async with AsyncSessionLocal() as db:
            wh = Webhook(tenant_id=w.ta, name="Old SIEM", key_prefix=old_key[:8],
                         key_hash=hashlib.sha256(old_key.encode()).hexdigest())
            db.add(wh)
            await db.commit()
            wid = wh.id
        try:
            listed = {x["id"]: x for x in (await c.get("/api/v1/integrations/webhooks", headers=admin)).json()}
            assert listed[wid]["needs_rotation"] is True and listed[wid]["api_key"] is None
            r = await c.post(f"/api/v1/integrations/webhooks/{wid}/rotate", headers=admin)
            assert r.status_code == 200 and r.json()["id"] == wid and r.json()["needs_rotation"] is False
            new_key = r.json()["api_key"]
            assert new_key.startswith("whk_") and r.json()["key_prefix"] == new_key[:12]
            alert = {"external_id": f"rot-{wid}", "title": "t", "payload": {}}
            assert (await c.post("/api/v1/alerts/webhook", json=alert, headers={"X-API-Key": new_key})).status_code == 200
            assert (await c.post(f"/api/v1/integrations/webhooks/{wid}/rotate",
                                 headers=hdr(w, "analyst"))).status_code == 403
            assert (await c.post(f"/api/v1/integrations/webhooks/{wid}/rotate",
                                 headers=hdr(w, "admin_b"))).status_code in (403, 404)  # other tenant
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Alert).where(Alert.tenant_id == w.ta, Alert.external_id == f"rot-{wid}"))
                await db.execute(delete(Webhook).where(Webhook.id == wid))
                await db.commit()
    run(body)
    asyncio.run(engine.dispose())
