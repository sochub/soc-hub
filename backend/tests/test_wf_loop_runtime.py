import asyncio
import secrets

from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.main import app  # noqa: F401  (registers all models)
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.workflows.runtime import finalize_run

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}}], "edges": []}


async def _scenario():
    h = secrets.token_hex(3)
    tenant_id = wf_id = None
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}")
            db.add(t)
            await db.flush()
            tenant_id = t.id
            wf = Workflow(tenant_id=tenant_id, name="loop-rt", trigger_type="manual", graph=GRAPH, enabled=False, version=1)
            db.add(wf)
            await db.flush()
            wf_id = wf.id
            parent = WorkflowRun(tenant_id=tenant_id, workflow_id=wf_id, workflow_version=1, status="waiting",
                                 trigger_payload={}, graph_snapshot=GRAPH)
            db.add(parent)
            await db.flush()
            step = WorkflowRunStep(run_id=parent.id, node_id="loop", status="waiting", output={"concurrency": 2, "total": 3})
            db.add(step)
            await db.flush()
            kids = [WorkflowRun(tenant_id=tenant_id, workflow_id=wf_id, workflow_version=1, status=s, trigger_payload={},
                                graph_snapshot=GRAPH, parent_run_id=parent.id, parent_step_id=step.id, loop_index=i)
                    for i, s in enumerate(["queued", "queued", "running"])]
            db.add_all(kids)
            parent_id, step_id = parent.id, step.id
            await db.commit()

            parent = await db.get(WorkflowRun, parent_id)
            await finalize_run(db, parent, "failed", "boom")
            await db.commit()

            db.expire_all()
            statuses = (await db.execute(select(WorkflowRun.status).where(WorkflowRun.parent_run_id == parent_id))).scalars().all()
            assert statuses == ["cancelled"] * 3, statuses
            assert (await db.get(WorkflowRunStep, step_id)).status == "cancelled"
        finally:
            await db.rollback()
            if wf_id:
                await db.execute(delete(Workflow).where(Workflow.id == wf_id))  # runs/steps cascade
            if tenant_id:
                await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
                await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
    await engine.dispose()


def test_finalize_cancels_loop_children():
    asyncio.run(_scenario())
