"""execute_step binds a fresh secret nonce per step render; step.input carries nonce-bound placeholders."""
import asyncio
import re
import secrets

import pytest
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.workflows import runtime
from app.workflows.nodes import EXECUTORS

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                   {"id": "call", "type": "http_request",
                    "config": {"method": "GET", "url": "https://api.example.test/x",
                               "headers": {"Authorization": "Bearer {{ secrets.API_TOKEN }}"}}}],
         "edges": [{"id": "e1", "source": "start", "target": "call", "source_handle": None}]}
PH = re.compile(r"^Bearer ⟦secret:API_TOKEN#([0-9a-f]{16})⟧$")


async def _scenario(monkeypatch):
    import app.tasks.workflows as tw
    h = secrets.token_hex(3)
    ids = {"t": [], "w": [], "r": [], "s": []}
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: None)
    async def fake_http(nctx, config):
        return {"status": 200}
    monkeypatch.setitem(EXECUTORS, "http_request", fake_http)
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}")
            db.add(t)
            await db.flush()
            ids["t"] = [t.id]
            wf = Workflow(tenant_id=t.id, name="wf-test", trigger_type="manual", graph=GRAPH)
            db.add(wf)
            await db.flush()
            ids["w"] = [wf.id]
            run = WorkflowRun(tenant_id=t.id, workflow_id=wf.id, workflow_version=1, status="running",
                              trigger_payload={}, graph_snapshot=GRAPH)
            db.add(run)
            await db.flush()
            ids["r"] = [run.id]
            step = WorkflowRunStep(run_id=run.id, node_id="call", status="pending")
            db.add(step)
            await db.flush()
            ids["s"] = [step.id]
            await db.commit()

            nonces = []
            for _ in range(2):  # second pass = a retry of the same step
                await runtime.execute_step(ids["s"][0])
                db.expire_all()
                st = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == ids["s"][0]))).scalars().one()
                assert st.status == "succeeded", st.error
                m = PH.match(st.input["headers"]["Authorization"])
                assert m, st.input
                nonces.append(m.group(1))
                st.status = "pending"
                await db.commit()
            assert nonces[0] != nonces[1], "each render (incl. retries) must get its own nonce"
        finally:
            await db.rollback()
            await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.id.in_(ids["s"])))
            await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(ids["r"])))
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["w"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
    await engine.dispose()


def test_each_step_render_gets_fresh_nonce(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
