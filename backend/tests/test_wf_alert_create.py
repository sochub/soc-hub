"""alert_create node: idempotent on (tenant, source, external_id); alert.ingested fires once per new alert.
Runs against the dev Postgres; cleans up only the rows it created."""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

import app.db.base  # noqa: F401
from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.case import Alert
from app.models.tenant import Tenant
from app.workflows import dryrun
from app.workflows.nodes import NodeContext, NodeError
from app.workflows.nodes import alerts as alerts_node

CTX = {"trigger": {"evt": {"id": "E-1", "sev": "HIGH", "user": "bob"}, "text": "not a dict"}}


def _nctx(db, tid):
    return NodeContext(db=db, run=SimpleNamespace(id=-1, tenant_id=tid, depth=0, case_id=None, alert_id=None),
                       step=None, node={}, ctx=CTX)


async def _scenario(monkeypatch):
    emitted = []
    monkeypatch.setattr(alerts_node, "emit_event", lambda *a, **k: emitted.append((a, k)))
    tids, alert_ids = [], []
    try:
        async with AsyncSessionLocal() as db:
            for _ in range(2):
                slug = f"wf-test-{uuid.uuid4().hex[:8]}"
                t = Tenant(name=slug, slug=slug)
                db.add(t)
                await db.flush()
                tids.append(t.id)
            await db.commit()
        # as rendered by the runtime: templated keys resolved, raw `payload` still an expression
        cfg = {"source": " okta ", "external_id": "E-1", "title": "Login by bob", "severity": "high", "payload": "trigger.evt"}

        async def run(tid, c):
            async with AsyncSessionLocal() as db:
                nctx = _nctx(db, tid)
                out = await alerts_node.run_alert_create(nctx, c)
                await db.commit()
                for fn in nctx.after_commit:
                    fn()
                return out

        first = await run(tids[0], cfg)
        alert_ids.append(first["alert_id"])
        assert first["created"] is True
        second = await run(tids[0], cfg)
        assert second == {"alert_id": first["alert_id"], "created": False}
        assert len(emitted) == 1
        args, kw = emitted[0]
        assert args == (tids[0], "alert.ingested") and kw == {"alert_id": first["alert_id"], "depth": 1}

        other = await run(tids[1], cfg)
        alert_ids.append(other["alert_id"])
        assert other["created"] is True and other["alert_id"] != first["alert_id"]

        bogus = await run(tids[0], {**cfg, "external_id": "E-2", "severity": "bogus"})
        alert_ids.append(bogus["alert_id"])
        assert bogus["severity_defaulted"] is True
        async with AsyncSessionLocal() as db:
            rows = {a.id: a for a in (await db.execute(select(Alert).where(Alert.id.in_(alert_ids)))).scalars()}
            assert rows[bogus["alert_id"]].severity == "medium"
            a = rows[first["alert_id"]]
            assert (a.source, a.status, a.severity, a.payload) == ("okta", "pending", "high", CTX["trigger"]["evt"])
            audits = (await db.execute(select(AuditLog).where(
                AuditLog.entity_type == "alert", AuditLog.entity_id == first["alert_id"]))).scalars().all()
            assert [x.action for x in audits] == ["created_by_workflow"]

        with pytest.raises(NodeError):
            await run(tids[0], {**cfg, "external_id": "E-3", "payload": "trigger.text"})
        with pytest.raises(NodeError):
            await run(tids[0], {**cfg, "external_id": "   "})
        with pytest.raises(NodeError):
            await run(tids[0], {**cfg, "source": "x" * 101})
    finally:
        async with AsyncSessionLocal() as db:
            ids = [i for i in alert_ids if i]
            if ids:
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "alert", AuditLog.entity_id.in_(ids)))
                await db.execute(delete(Alert).where(Alert.id.in_(ids)))
            if tids:
                await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
        await engine.dispose()


def test_alert_create(monkeypatch):
    asyncio.run(_scenario(monkeypatch))


def test_alert_create_simulated_in_dry_run():
    node = {"id": "n", "type": "alert_create", "config": {}}
    assert dryrun.dry_run_behaviour(node) == "simulate"
    out = dryrun.simulated_output(node, {"source": "okta"}, {})
    assert out["alert_id"] is None and out["created"] is True and out["simulated"] is True


def test_fail_node_raises_with_message():
    import asyncio as _a
    from types import SimpleNamespace
    from app.workflows.nodes import EXECUTORS, NodeError as _NE
    nctx = SimpleNamespace()
    try:
        _a.run(EXECUTORS["fail"](nctx, {"message": "Pull failed: HTTP 429"}))
    except _NE as e:
        assert str(e) == "Pull failed: HTTP 429"
    else:
        raise AssertionError("fail node did not raise")
