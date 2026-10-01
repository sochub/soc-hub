import secrets

import pytest
from sqlalchemy import delete

from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact, ArtifactType
from app.models.ioc import IOC
from app.models.tenant import Tenant


@pytest.mark.asyncio
async def test_hook_enqueues_on_create_and_relevant_changes(_no_real_enrichment_enqueue):
    calls = _no_real_enrichment_enqueue
    await engine.dispose()  # drop pooled connections a previous test's loop may have left behind
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
        db.add(t)
        await db.commit()
        tid = t.id  # rollback below expires `t`; keep the id for cleanup
        try:
            ioc = IOC(tenant_id=t.id, ioc_type="domain", value="evil.example", tlp="amber")
            art = Artifact(tenant_id=t.id, artifact_type=ArtifactType.DOMAIN, value="art.example")
            db.add_all([ioc, art])
            await db.commit()
            assert ((t.id, "domain", "evil.example", "amber", False), {}) in calls
            assert ((t.id, "domain", "art.example", None, False), {}) in calls
            calls.clear()
            ioc.description = "notes only"
            await db.commit()
            assert calls == []
            ioc.tlp = "green"
            await db.commit()
            assert calls == [((t.id, "domain", "evil.example", "green", False), {})]
            calls.clear()
            ioc.value = "other.example"
            await db.rollback()
            assert calls == []
        finally:
            await db.execute(delete(IOC).where(IOC.tenant_id == tid))
            await db.execute(delete(Artifact).where(Artifact.tenant_id == tid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_kill_switch(monkeypatch, _no_real_enrichment_enqueue):
    import app.enrichment.hooks as hooks
    from app.core.config import settings
    monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", False)
    hooks.enqueue(1, "domain", "x.com", "white")
    assert _no_real_enrichment_enqueue == []
