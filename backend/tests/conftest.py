import pytest


@pytest.fixture(autouse=True)
def _no_real_enrichment_enqueue(monkeypatch):
    calls = []
    import app.enrichment.hooks as hooks
    monkeypatch.setattr(hooks, "_delay", lambda *a, **k: calls.append((a, k)))
    # Commits with external notifications must never reach the real broker either.
    import app.notifications.delivery as delivery
    monkeypatch.setattr(delivery, "_enqueue", lambda ids: None)
    # Guest digests: never touch real Redis or the broker in tests.
    import app.guests.notify as gnotify

    class _SyncRedis:
        def __init__(self):
            self.keys = set()

        def set(self, k, v, nx=False, ex=None):
            if nx and k in self.keys:
                return None
            self.keys.add(k)
            return True

        def delete(self, k):
            self.keys.discard(k)

    fake = _SyncRedis()
    monkeypatch.setattr(gnotify, "_redis", lambda: fake)
    monkeypatch.setattr(gnotify, "_dispatch", lambda gid: None)
    # SAML assertion replay cache: a per-test fake, never real Redis.
    import app.api.v1.sso as sso_mod

    class _AsyncRedis:
        def __init__(self):
            self.keys = {}

        async def set(self, k, v, nx=False, ex=None):
            if nx and k in self.keys:
                return None
            self.keys[k] = ex
            return True

    saml_fake = _AsyncRedis()
    monkeypatch.setattr(sso_mod, "_saml_redis", lambda: saml_fake)
    yield calls


@pytest.fixture(autouse=True)
def _cleanup_audit_rows(monkeypatch):
    """Safety net: every audit row inserted via the middleware/audit_event path during a test is deleted
    at teardown, by id only. Rows in real tenants are also removed by tenant cascade."""
    import asyncio

    from app.audit import middleware as mw
    ids = []
    real = mw._insert

    async def tracking(**fields):
        new_id = await real(**fields)
        if new_id is not None:
            ids.append(new_id)
        return new_id

    monkeypatch.setattr(mw, "_insert", tracking)
    yield
    if not ids:
        return

    async def purge():
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.config import settings
        from app.models.audit_log import AuditLog
        eng = create_async_engine(settings.DATABASE_URL)
        try:
            async with eng.begin() as conn:
                await conn.execute(delete(AuditLog).where(AuditLog.id.in_(ids)))
        finally:
            await eng.dispose()
    asyncio.run(purge())
