"""Beat reaper for workflow steps left `running` by a hard kill / worker crash.
Cleanup deletes ONLY rows whose ids were collected here."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                   {"id": "a", "type": "set_variable", "config": {}}],
         "edges": [{"id": "e1", "source": "start", "target": "a", "source_handle": None}]}
REASON = "step did not finish (worker limit or crash)"


def _now():
    return datetime.now(timezone.utc)


class Ids:
    def __init__(self):
        self.h = secrets.token_hex(3)
        self.tenants, self.wf, self.runs = [], [], []


async def setup(db, ids):
    t = Tenant(name="reap-test", slug=f"reap-{ids.h}", is_active=True)
    db.add(t)
    await db.flush()
    ids.tenants.append(int(t.id))
    w = Workflow(tenant_id=t.id, name="reap", enabled=True, trigger_type="manual", graph=GRAPH)
    db.add(w)
    await db.flush()
    ids.wf.append(int(w.id))
    return t, w


async def run_with_step(db, ids, t, w, status, age_s, *, parent_step_id=None, loop_index=None, run_status="running"):
    r = WorkflowRun(tenant_id=t.id, workflow_id=w.id, workflow_version=1, status=run_status, graph_snapshot=GRAPH,
                    trigger_payload={}, parent_step_id=parent_step_id, loop_index=loop_index)
    db.add(r)
    await db.flush()
    ids.runs.append(int(r.id))
    db.add(WorkflowRunStep(run_id=r.id, node_id="start", status="succeeded", output={}, started_at=_now(), finished_at=_now()))
    s = WorkflowRunStep(run_id=r.id, node_id="a", status=status, started_at=_now() - timedelta(seconds=age_s))
    db.add(s)
    await db.flush()
    return r, s


async def cleanup(ids):
    async with AsyncSessionLocal() as db:
        runs = ids.runs or [-1]
        await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.run_id.in_(runs)))
        await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(runs)))
        await db.execute(delete(Workflow).where(Workflow.id.in_(ids.wf or [-1])))
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids.tenants or [-1])))
        await db.execute(delete(Tenant).where(Tenant.id.in_(ids.tenants or [-1])))
        await db.commit()


def scenario(fn):
    def test(monkeypatch):
        async def main():
            ids = Ids()
            try:
                await fn(ids, monkeypatch)
            finally:
                await cleanup(ids)
                await engine.dispose()
        asyncio.run(main())
    test.__name__ = fn.__name__
    return test


def capture(monkeypatch):
    from app.tasks import workflows as tw
    sent = {"advance": [], "tick": []}
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: sent["advance"].append(rid))
    monkeypatch.setattr(tw.loop_tick_task, "delay", lambda sid: sent["tick"].append(sid))
    return sent


def limit():
    from app.tasks import workflows as tw
    from app.workflows import runtime
    return runtime.step_hard_limit_seconds() + runtime.REAP_MARGIN_SECONDS


async def get(model, id_):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(model).where(model.id == id_))).scalars().first()


@scenario
async def test_stuck_running_step_failed_and_run_finalized(ids, monkeypatch):
    from app.workflows import runtime
    sent = capture(monkeypatch)
    async with AsyncSessionLocal() as db:
        t, w = await setup(db, ids)
        r, s = await run_with_step(db, ids, t, w, "running", limit() + 60)
        await db.commit()
        rid, sid = r.id, s.id
    assert await runtime.reap_stuck_steps() == [sid]
    st = await get(WorkflowRunStep, sid)
    assert st.status == "failed" and st.error == REASON and st.finished_at is not None
    assert sent["advance"] == [rid]
    await runtime.advance_run(rid)  # what the queued task does
    run = await get(WorkflowRun, rid)
    assert run.status == "failed" and REASON in run.error


@scenario
async def test_loop_child_slot_released(ids, monkeypatch):
    from app.workflows import runtime
    sent = capture(monkeypatch)
    async with AsyncSessionLocal() as db:
        t, w = await setup(db, ids)
        parent, pstep = await run_with_step(db, ids, t, w, "waiting", 0)
        pstep.output = {"concurrency": 1}
        await db.flush()
        c0, s0 = await run_with_step(db, ids, t, w, "running", limit() + 60, parent_step_id=pstep.id, loop_index=0)
        c1, _ = await run_with_step(db, ids, t, w, "pending", 0, parent_step_id=pstep.id, loop_index=1, run_status="queued")
        await db.commit()
        pstep_id, c0id, c1id, s0id = pstep.id, c0.id, c1.id, s0.id
    assert await runtime.reap_stuck_steps() == [s0id]
    await runtime.advance_run(c0id)
    assert (await get(WorkflowRun, c0id)).status == "failed"
    assert sent["tick"] == [pstep_id]
    await runtime.loop_tick(pstep_id)
    assert (await get(WorkflowRun, c1id)).status == "running"  # the freed slot started the next child


@scenario
async def test_recent_running_and_waiting_untouched(ids, monkeypatch):
    from app.workflows import runtime
    sent = capture(monkeypatch)
    async with AsyncSessionLocal() as db:
        t, w = await setup(db, ids)
        r1, recent = await run_with_step(db, ids, t, w, "running", 5)
        r2, waiting = await run_with_step(db, ids, t, w, "waiting", limit() + 600)
        await db.commit()
        rec_id, wait_id = recent.id, waiting.id
    assert await runtime.reap_stuck_steps() == []
    assert (await get(WorkflowRunStep, rec_id)).status == "running"
    assert (await get(WorkflowRunStep, wait_id)).status == "waiting"
    assert sent["advance"] == []


@scenario
async def test_reaper_idempotent(ids, monkeypatch):
    from app.workflows import runtime
    sent = capture(monkeypatch)
    async with AsyncSessionLocal() as db:
        t, w = await setup(db, ids)
        r, s = await run_with_step(db, ids, t, w, "running", limit() + 60)
        await db.commit()
        sid = s.id
    assert await runtime.reap_stuck_steps() == [sid]
    first = await get(WorkflowRunStep, sid)
    assert await runtime.reap_stuck_steps() == []
    second = await get(WorkflowRunStep, sid)
    assert (second.status, second.error, second.finished_at) == (first.status, first.error, first.finished_at)
    assert len(sent["advance"]) == 1
