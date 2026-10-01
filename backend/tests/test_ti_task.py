import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal, engine
from app.enrichment.config import EnrichmentSettings
from app.enrichment.sources.base import LookupResult
from app.models.enrichment_result import EnrichmentResult
from app.models.tenant import Tenant
import app.tasks.enrichment as task

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


class FakeLimiter:
    def __init__(self, waits=None):
        self.waits = waits or {}

    async def acquire(self, tid, source, limits):
        return self.waits.get(source)


class FakeSource:
    def __init__(self, name, types, result=None, exc=None):
        self.NAME, self.TYPES, self.result, self.exc, self.calls = name, frozenset(types), result, exc, 0

    async def lookup(self, itype, value, key=None, **kw):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


@pytest_asyncio.fixture
async def tenant():
    await engine.dispose()  # drop pooled connections a previous test's loop may have left behind
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
        db.add(t)
        await db.commit()
        tid = t.id
    yield tid
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tid))
        await db.commit()
    await engine.dispose()


def patch_world(monkeypatch, sources, settings=None, delays=None):
    monkeypatch.setattr(task, "REGISTRY", {s.NAME: s for s in sources})

    async def fake_settings(db, tid):
        return settings or EnrichmentSettings(sources={s.NAME: True for s in sources})
    monkeypatch.setattr(task, "load_settings", fake_settings)
    monkeypatch.setattr(task, "runnable_sources", lambda s: [n for n in s.sources if s.sources[n]])
    monkeypatch.setattr(task, "_retry", lambda *a, **k: (delays if delays is not None else []).append((a, k)))


async def rows(tid):
    async with AsyncSessionLocal() as db:
        return {r.source: r for r in (await db.execute(select(EnrichmentResult).where(
            EnrichmentResult.tenant_id == tid))).scalars()}


@pytest.mark.asyncio
async def test_sources_independent_and_logged(tenant, monkeypatch, caplog):
    good = FakeSource("rdap", {"domain"}, LookupResult(status="ok", verdict="unknown", summary={"x": 1}))
    bad = FakeSource("crtsh", {"domain"}, exc=RuntimeError("boom secret-key-123"))
    patch_world(monkeypatch, [good, bad], EnrichmentSettings(sources={"rdap": True, "crtsh": True},
                                                              keys={"virustotal_api_key": "secret-key-123"}))
    caplog.set_level(logging.INFO, logger="app.tasks.enrichment")
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW)
    assert out == {"rdap": "ok", "crtsh": "error"}
    rs = await rows(tenant)
    assert rs["rdap"].verdict == "unknown" and rs["rdap"].fetched_at is not None
    assert "secret-key-123" not in rs["crtsh"].error
    lines = [r.getMessage() for r in caplog.records if r.name == "app.tasks.enrichment"]
    assert len(lines) == 2 and all(l.startswith(f"ti_lookup tenant={tenant} source=") for l in lines)
    assert not any("example.com" in l for l in lines)


@pytest.mark.asyncio
async def test_cache_fresh_skips_and_force_refreshes(tenant, monkeypatch):
    src = FakeSource("rdap", {"domain"}, LookupResult(status="ok", verdict="unknown"))
    patch_world(monkeypatch, [src])
    async with AsyncSessionLocal() as db:
        await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW)
        await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW + timedelta(hours=1))
        assert src.calls == 1
        await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW + timedelta(hours=25))
        assert src.calls == 2
        await task.run_enrichment(db, tenant, "domain", "example.com", force=True, limiter=FakeLimiter(), now=NOW + timedelta(hours=25))
        assert src.calls == 3


@pytest.mark.asyncio
async def test_private_ip_skipped_without_call(tenant, monkeypatch):
    src = FakeSource("rdap", {"ip"}, LookupResult(status="ok"))
    patch_world(monkeypatch, [src])
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "ip", "10.0.0.1", limiter=FakeLimiter(), now=NOW)
    assert out == {"rdap": "skipped"} and src.calls == 0


@pytest.mark.asyncio
async def test_minute_limit_retries_daily_limit_stops(tenant, monkeypatch):
    vt = FakeSource("virustotal", {"ip"}, LookupResult(status="ok"))
    delays = []
    patch_world(monkeypatch, [vt], delays=delays)
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "ip", "8.8.8.8", limiter=FakeLimiter({"virustotal": 30}), now=NOW)
        assert out == {"virustotal": "pending"} and len(delays) == 1 and delays[0][1]["countdown"] == 30
        out = await task.run_enrichment(db, tenant, "ip", "8.8.8.8", force=True,
                                        limiter=FakeLimiter({"virustotal": 40000}), now=NOW)
    assert out == {"virustotal": "rate_limited"} and vt.calls == 0
    assert (await rows(tenant))["virustotal"].error == "VirusTotal daily quota reached"


@pytest.mark.asyncio
async def test_retry_attempt_cap(tenant, monkeypatch):
    vt = FakeSource("virustotal", {"ip"}, LookupResult(status="ok"))
    delays = []
    patch_world(monkeypatch, [vt], delays=delays)
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "ip", "8.8.8.8", force=True, attempt=10,
                                        limiter=FakeLimiter({"virustotal": 30}), now=NOW)
    assert out == {"virustotal": "rate_limited"} and delays == []


@pytest.mark.asyncio
async def test_key_decrypt_failure_marks_keyed_sources(tenant, monkeypatch):
    rd = FakeSource("rdap", {"domain"}, LookupResult(status="ok", verdict="unknown"))
    patch_world(monkeypatch, [rd], EnrichmentSettings(sources={"rdap": True, "virustotal": True}, key_error=True))
    monkeypatch.setattr(task, "runnable_sources", lambda s: ["rdap"])
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW)
    assert out["virustotal"] == "error" and out["rdap"] == "ok"
    assert "re-enter in Integrations" in (await rows(tenant))["virustotal"].error


@pytest.mark.asyncio
async def test_dedupe_lock(monkeypatch):
    seen = []

    async def fake_run(db, *a, **k):
        seen.append(a)
        return {}
    monkeypatch.setattr(task, "run_enrichment", fake_run)

    async def fake_settings(db, tid):
        return EnrichmentSettings()
    monkeypatch.setattr(task, "load_settings", fake_settings)

    class Lock:
        def __init__(self):
            self.held = set()

        async def acquire(self, key):
            if key in self.held:
                return False
            self.held.add(key)
            return True
    lock = Lock()
    monkeypatch.setattr(task, "_lock", lambda: lock)
    await task._entry(1, "domain", "Example.com", "white", False)
    await task._entry(1, "domain", "example.com", "white", False)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_entry_tlp_gating_and_artifact_default(monkeypatch):
    seen = []

    async def fake_run(db, *a, **k):
        seen.append(a)
        return {}
    monkeypatch.setattr(task, "run_enrichment", fake_run)

    async def fake_settings(db, tid):
        return EnrichmentSettings(auto_max_tlp="green", artifact_tlp="amber")
    monkeypatch.setattr(task, "load_settings", fake_settings)

    class Always:
        async def acquire(self, key):
            return True
    monkeypatch.setattr(task, "_lock", lambda: Always())
    await task._entry(1, "domain", "a.com", "amber", False)     # auto + amber > green -> no run
    await task._entry(1, "domain", "b.com", None, False)        # artifact -> amber -> no run
    await task._entry(1, "domain", "c.com", "green", False)     # runs
    await task._entry(1, "domain", "d.com", "red", True)        # forced -> runs
    await task._entry(1, "email", "x@y.com", "white", True)     # not enrichable -> no run
    assert [a[2] for a in seen] == ["c.com", "d.com"]
