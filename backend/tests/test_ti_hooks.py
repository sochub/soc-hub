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


def test_hooks_registered_in_worker_process():
    # Fresh interpreter: conftest already imports the hooks in this process, which would hide the bug.
    import subprocess
    import sys
    code = ("import app.worker\n"
            "from sqlalchemy import event\n"
            "from sqlalchemy.orm import Session\n"
            "import sys\n"
            "assert 'app.main' not in sys.modules, 'worker must not need the API module'\n"
            "hooks = sys.modules.get('app.enrichment.hooks')\n"
            "assert hooks is not None, 'hooks not imported by the worker'\n"
            "assert event.contains(Session, 'after_flush', hooks._collect)\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]


async def _mk_tenant(db):
    t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
    db.add(t)
    await db.commit()
    return t.id


async def _cleanup(tid):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(IOC).where(IOC.tenant_id == tid))
        await db.execute(delete(Tenant).where(Tenant.id == tid))
        await db.commit()


@pytest.mark.asyncio
async def test_several_flushes_one_commit_enqueue_each(_no_real_enrichment_enqueue):
    calls = _no_real_enrichment_enqueue
    await engine.dispose()
    tid = None
    try:
        async with AsyncSessionLocal() as db:
            tid = await _mk_tenant(db)
            calls.clear()
            for i in range(5):
                db.add(IOC(tenant_id=tid, ioc_type="domain", value=f"n{i}.example", tlp="white"))
                await db.flush()
            await db.commit()
        assert sorted(a[2] for a, _ in calls) == [f"n{i}.example" for i in range(5)]
    finally:
        if tid:
            await _cleanup(tid)
        await engine.dispose()


@pytest.mark.asyncio
async def test_savepoint_rollback_keeps_outer_item(_no_real_enrichment_enqueue):
    calls = _no_real_enrichment_enqueue
    await engine.dispose()
    tid = None
    try:
        async with AsyncSessionLocal() as db:
            tid = await _mk_tenant(db)
            calls.clear()
            db.add(IOC(tenant_id=tid, ioc_type="domain", value="outer.example", tlp="white"))
            await db.flush()
            sp = await db.begin_nested()
            db.add(IOC(tenant_id=tid, ioc_type="domain", value="inner.example", tlp="white"))
            await db.flush()
            await sp.rollback()
            assert calls == []
            sp2 = await db.begin_nested()
            await sp2.commit()  # releasing a savepoint must not enqueue anything yet
            assert calls == []
            await db.commit()
        assert [a[2] for a, _ in calls].count("outer.example") == 1
    finally:
        if tid:
            await _cleanup(tid)
        await engine.dispose()
