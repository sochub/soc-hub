"""Celery time limits: one tenant's stuck task can't hold the single shared worker forever, and a workflow
step that hits the soft limit fails cleanly ("step timed out") so its run finalizes."""
import asyncio
import secrets

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.workflows import runtime
from app.workflows.nodes import EXECUTORS

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                   {"id": "work", "type": "http_request", "config": {"url": "https://example.test"}}],
         "edges": [{"id": "e1", "source": "start", "target": "work", "source_handle": None}]}


def test_worker_has_default_time_limits():
    from app.worker import celery_app
    conf = celery_app.conf
    assert conf.task_soft_time_limit and conf.task_time_limit
    assert conf.task_soft_time_limit < conf.task_time_limit
    assert conf.task_time_limit <= 300


def test_long_running_tasks_have_longer_overrides():
    from app.worker import celery_app
    import app.tasks.triage as triage
    import app.tasks.enrichment as enrichment
    import app.tasks.sla as sla
    default_soft = celery_app.conf.task_soft_time_limit
    for task in (triage.run_case_triage_task, enrichment.enrich_indicator, sla.check_sla_breaches_task):
        assert task.soft_time_limit and task.soft_time_limit > default_soft, task.name
        assert task.time_limit and task.time_limit > task.soft_time_limit, task.name


async def _setup(h, step_status="pending"):
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"tl-test-{h}")
        db.add(t)
        await db.flush()
        wf = Workflow(tenant_id=t.id, name="wf-test", trigger_type="manual", graph=GRAPH)
        db.add(wf)
        await db.flush()
        run = WorkflowRun(tenant_id=t.id, workflow_id=wf.id, workflow_version=1, status="running",
                          trigger_payload={}, graph_snapshot=GRAPH)
        db.add(run)
        await db.flush()
        db.add(WorkflowRunStep(run_id=run.id, node_id="start", status="succeeded", output={}))
        step = WorkflowRunStep(run_id=run.id, node_id="work", status=step_status)
        db.add(step)
        await db.flush()
        ids = {"t": t.id, "w": wf.id, "r": run.id, "s": step.id}
        await db.commit()
    await engine.dispose()
    return ids


async def _state(ids):
    async with AsyncSessionLocal() as db:
        st = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == ids["s"]))).scalars().one()
        rn = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == ids["r"]))).scalars().one()
        out = (st.status, st.error, st.finished_at, rn.status, rn.error)
    await engine.dispose()
    return out


async def _cleanup(ids):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.run_id == ids["r"]))
        await db.execute(delete(WorkflowRun).where(WorkflowRun.id == ids["r"]))
        await db.execute(delete(Workflow).where(Workflow.id == ids["w"]))
        await db.execute(delete(Tenant).where(Tenant.id == ids["t"]))
        await db.commit()
        left = (await db.execute(select(func.count()).select_from(Tenant).where(
            Tenant.slug == f"tl-test-{ids['h']}"))).scalar()
        assert left == 0
    await engine.dispose()


def _run(coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await engine.dispose()  # pooled connections are bound to this asyncio.run() loop
    return asyncio.run(wrapper())


def _patch_tasks(monkeypatch):
    import app.tasks.workflows as tw
    advanced, executed = [], []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: advanced.append(rid))
    monkeypatch.setattr(tw.execute_step_task, "delay", lambda sid: executed.append(sid))
    monkeypatch.setattr(tw.execute_step_task, "apply_async", lambda *a, **k: executed.append(a))
    return advanced, executed


def test_step_soft_time_limit_inside_executor_fails_step_and_run(monkeypatch):
    advanced, _ = _patch_tasks(monkeypatch)

    async def slow_node(nctx, config):
        raise SoftTimeLimitExceeded()

    monkeypatch.setitem(EXECUTORS, "http_request", slow_node)
    h = secrets.token_hex(3)
    ids = asyncio.run(_setup(h))
    ids["h"] = h
    try:
        _run(runtime.execute_step(ids["s"]))
        status, error, finished, _, _ = asyncio.run(_state(ids))
        assert status == "failed" and finished is not None
        assert error == "step timed out"
        assert advanced == [ids["r"]], "the run must be advanced so it finalizes"
        _run(runtime.advance_run(ids["r"]))
        _, _, _, run_status, run_error = asyncio.run(_state(ids))
        assert run_status == "failed"
        assert "timed out" in (run_error or "")
    finally:
        asyncio.run(_cleanup(ids))


def test_step_soft_time_limit_escaping_the_event_loop_is_handled_by_the_task(monkeypatch):
    """The soft-limit signal can land outside the step coroutine (in the event loop itself); the task
    wrapper must still mark the step failed and advance the run."""
    advanced, _ = _patch_tasks(monkeypatch)
    import app.tasks.workflows as tw

    async def escaping_execute_step(step_id):
        async with AsyncSessionLocal() as db:
            st = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id))).scalars().one()
            st.status = "running"
            await db.commit()
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(runtime, "execute_step", escaping_execute_step)
    h = secrets.token_hex(3)
    ids = asyncio.run(_setup(h))
    ids["h"] = h
    try:
        tw.execute_step_task.run(ids["s"])  # must not raise
        status, error, finished, _, _ = asyncio.run(_state(ids))
        assert status == "failed" and error == "step timed out" and finished is not None
        assert advanced == [ids["r"]]
    finally:
        asyncio.run(_cleanup(ids))
