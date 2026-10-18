"""Cron-scheduled workflows: the matcher, save-time validation and the idempotent sweep."""
import asyncio
import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.workflows.cron import cron_errors, cron_matches
from app.workflows.validation import workflow_errors

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "position": {"x": 0, "y": 0}, "config": {}}], "edges": []}


def _at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_cron_matches():
    assert cron_matches("*/15 * * * *", _at(2026, 10, 19, 10, 30))
    assert not cron_matches("*/15 * * * *", _at(2026, 10, 19, 10, 31))
    assert cron_matches("0 9 * * 1-5", _at(2026, 10, 19, 9, 0))  # Monday
    assert not cron_matches("0 9 * * 1-5", _at(2026, 10, 17, 9, 0))  # Saturday
    assert cron_matches("30 2 1 * *", _at(2026, 11, 1, 2, 30))
    assert not cron_matches("30 2 1 * *", _at(2026, 11, 2, 2, 30))
    assert not cron_matches("30 2 1 * *", _at(2026, 11, 1, 3, 30))
    assert cron_matches("0 0 * * 7", _at(2026, 10, 18, 0, 0))  # 7 = Sunday


def test_cron_errors():
    assert any("5 fields" in m for m in cron_errors("* * * *"))
    assert any("range" in m for m in cron_errors("60 * * * *"))
    assert cron_errors("a * * * *")
    assert cron_errors("*/0 * * * *")
    assert cron_errors("*/15 * * * *") == []
    assert cron_errors("0,30 8-18/2 1-31 1-12 0-7") == []


def test_workflow_errors_schedule():
    assert workflow_errors(GRAPH, "schedule", "", None)
    assert workflow_errors(GRAPH, "schedule", None, None)
    assert workflow_errors(GRAPH, "schedule", "61 * * * *", None)
    assert workflow_errors(GRAPH, "schedule", "*/5 * * * *", None) == []
    assert workflow_errors(GRAPH, "manual", "", None) == []


async def _sweep(monkeypatch):
    import app.tasks.workflows as tw
    from app.workflows.runtime import run_due_schedules
    h = secrets.token_hex(3)
    ids = {"t": [], "w": []}
    advanced = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: advanced.append(rid))
    now = datetime.now(timezone.utc).replace(second=7, microsecond=0)
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}")
            db.add(t)
            await db.flush()
            ids["t"] = [t.id]
            on = Workflow(tenant_id=t.id, name="on", enabled=True, trigger_type="schedule",
                          trigger_filter="* * * * *", graph=GRAPH)
            off = Workflow(tenant_id=t.id, name="off", enabled=False, trigger_type="schedule",
                           trigger_filter="* * * * *", graph=GRAPH)
            db.add_all([on, off])
            await db.flush()
            ids["w"] = [on.id, off.id]
            await db.commit()

            first = await run_due_schedules(now)
            mine = (await db.execute(select(WorkflowRun).where(WorkflowRun.workflow_id.in_(ids["w"])))).scalars().all()
            assert len(mine) == 1 and mine[0].workflow_id == ids["w"][0]
            assert mine[0].id in first and mine[0].id in advanced
            assert mine[0].trigger_payload["event"] == "schedule" and mine[0].case_id is None

            # beat drift: a second tick in the same minute must not fire again
            second = await run_due_schedules(now.replace(second=55))
            assert mine[0].id not in second
            assert (await db.execute(select(WorkflowRun.id).where(
                WorkflowRun.workflow_id.in_(ids["w"])))).scalars().all() == [mine[0].id]
        finally:
            await db.rollback()
            run_ids = (await db.execute(select(WorkflowRun.id).where(WorkflowRun.workflow_id.in_(ids["w"])))).scalars().all()
            await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.run_id.in_(run_ids)))
            await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(run_ids)))
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["w"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["t"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
    await engine.dispose()


def test_run_due_schedules(monkeypatch):
    asyncio.run(_sweep(monkeypatch))
