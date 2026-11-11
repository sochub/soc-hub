"""Old L3: the alert webhook caps field sizes and payload size, and rate-limits each webhook key."""
import asyncio
import secrets

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import delete

from app.api.v1 import alerts as alerts_api
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.case import Alert
from app.models.tenant import Tenant
from app.models.webhook import Webhook, hash_webhook_key, webhook_key_prefix
from app.schemas.case import AlertWebhookCreate


def test_webhook_schema_caps():
    AlertWebhookCreate(external_id="a" * 255, title="t" * 500, payload={"x": "y" * 1000})
    for bad in ({"external_id": "a" * 256, "title": "t"}, {"external_id": "", "title": "t"},
                {"external_id": "a", "title": "t" * 501},
                {"external_id": "a", "title": "t", "payload": {"x": "y" * (256 * 1024)}}):
        with pytest.raises(ValidationError):
            AlertWebhookCreate(**bad)


class _Redis:
    def __init__(self):
        self.n = {}

    async def incr(self, k):
        self.n[k] = self.n.get(k, 0) + 1
        return self.n[k]

    async def expire(self, *a, **k):
        return True


def test_webhook_rate_limited_per_key(monkeypatch):
    async def body():
        await engine.dispose()
        monkeypatch.setattr(settings, "ALERT_WEBHOOK_PER_MINUTE", 2)
        h = secrets.token_hex(3)
        key = f"whk_{secrets.token_urlsafe(32)}"
        async with AsyncSessionLocal() as db:
            t = Tenant(name="whrl", slug=f"whrl-{h}")
            db.add(t)
            await db.flush()
            wh = Webhook(tenant_id=t.id, name="siem", key_prefix=webhook_key_prefix(key), key_hash=hash_webhook_key(key))
            db.add(wh)
            await db.commit()
            tid = t.id
        app.dependency_overrides[alerts_api.get_webhook_redis] = lambda: _Redis.shared
        _Redis.shared = _Redis()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                codes = [(await c.post("/api/v1/alerts/webhook", headers={"X-API-Key": key},
                                       json={"external_id": f"{h}-{i}", "title": "t"})).status_code for i in range(3)]
            assert codes == [200, 200, 429]
        finally:
            app.dependency_overrides.pop(alerts_api.get_webhook_redis, None)
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Alert).where(Alert.tenant_id == tid))
                await db.execute(delete(Webhook).where(Webhook.tenant_id == tid))
                await db.execute(delete(Tenant).where(Tenant.id == tid))
                await db.commit()
            await engine.dispose()
    asyncio.run(body())
