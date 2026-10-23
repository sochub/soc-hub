"""Webhook keys: Argon2 at rest, legacy SHA-256 rows upgraded on first use."""
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
from app.models.webhook import Webhook, verify_webhook_key


async def _scenario():
    h = secrets.token_hex(3)
    key = f"whk_{secrets.token_urlsafe(32)}"
    async with AsyncSessionLocal() as db:
        t = Tenant(name="whk-test", slug=f"whk-test-{h}")
        db.add(t)
        await db.flush()
        wh = Webhook(tenant_id=t.id, name="Legacy", key_prefix=key[:8],
                     key_hash=hashlib.sha256(key.encode()).hexdigest())  # as stored before Argon2
        db.add(wh)
        await db.commit()
        tid, wid = t.id, wh.id
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            async def post(k, n):
                return await c.post("/api/v1/alerts/webhook", headers={"X-API-Key": k},
                                    json={"external_id": f"{h}-{n}", "title": "t", "payload": {}})
            assert (await post(key, 1)).status_code == 200
            async with AsyncSessionLocal() as db:
                row = await db.get(Webhook, wid)
                assert row.key_hash.startswith("$argon2id$") and verify_webhook_key(key, row.key_hash)
                assert row.key_prefix == key[:12]
            deps._verified_webhooks.clear()  # next call must verify the Argon2 hash itself
            assert (await post(key, 2)).status_code == 200
            assert (await post(key, 3)).status_code == 200  # cached
            assert (await post(key[:-1] + ("A" if key[-1] != "A" else "B"), 4)).status_code == 401
            async with AsyncSessionLocal() as db:  # a deleted webhook's cached key stops working
                await db.execute(delete(Webhook).where(Webhook.id == wid))
                await db.commit()
            assert (await post(key, 5)).status_code == 401
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Alert).where(Alert.tenant_id == tid))
            await db.execute(delete(Webhook).where(Webhook.id == wid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()


def test_legacy_key_upgraded_and_cache_respects_deletion():
    asyncio.run(_scenario())
