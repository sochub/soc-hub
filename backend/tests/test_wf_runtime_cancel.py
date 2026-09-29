"""A run cancelled while one of its steps is executing must not be overwritten by that step."""
import asyncio
import secrets

from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.workflows import runtime
import pytest

from app.workflows.nodes import EXECUTORS, NodeError, WAIT

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                   {"id": "ask", "type": "slack_ask_user", "config": {"email": "a@example.test", "message": "hi"}}],
         "edges": [{"id": "e1", "source": "start", "target": "ask", "source_handle": None}]}


async def _scenario(monkeypatch, mode):
    import app.tasks.workflows as tw
    h = secrets.token_hex(3)
    ids = {"t": [], "w": [], "r": [], "s": []}
    advanced, retried, withdrawn, hooks = [], [], [], []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: advanced.append(rid))
    monkeypatch.setattr(tw.execute_step_task, "apply_async", lambda *a, **k: retried.append(a))
    monkeypatch.setattr(tw.withdraw_messages_task, "delay", lambda targets: withdrawn.extend(targets))

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
            step = WorkflowRunStep(run_id=run.id, node_id="ask", status="pending")
            db.add(step)
            await db.flush()
            ids["s"] = [step.id]
            tid, run_id, step_id = t.id, run.id, step.id
            await db.commit()

            async def cancelling_ask(nctx, config):
                # someone cancels the run while the node is talking to Slack
                async with AsyncSessionLocal() as other:
                    r = (await other.execute(select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update())).scalars().one()
                    await runtime.cancel_run(other, r)
                    await other.commit()
                nctx.db.add(AuditLog(entity_type="wf-test-marker", entity_id=0, action="node-write", tenant_id=tid))
                await nctx.db.flush()  # a node write that must be discarded
                nctx.step.wait_token = "tok-" + h
                nctx.step.output = {"target_slack_id": "U1", "channel": "D9", "message_ts": "7.7", "buttons": ["Yes"]}
                nctx.after_commit.append(lambda: hooks.append("ran"))
                if mode == "error":
                    raise NodeError("boom")
                return WAIT

            monkeypatch.setitem(EXECUTORS, "slack_ask_user", cancelling_ask)
            await runtime.execute_step(step_id)

            db.expire_all()
            st = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id))).scalars().one()
            rn = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalars().one()
            assert rn.status == "cancelled"
            assert st.status == "cancelled" and st.finished_at is not None, st.status
            assert st.wait_token is None
            assert hooks == [], "after_commit hooks of a cancelled step must not run"
            assert advanced == [] and retried == []
            # a DM that was posted is withdrawn; a node that failed has nothing to withdraw
            assert withdrawn == ([[tid, "D9", "7.7"]] if mode == "wait" else []), withdrawn
            markers = (await db.execute(select(func.count()).select_from(AuditLog).where(
                AuditLog.tenant_id == tid, AuditLog.entity_type == "wf-test-marker"))).scalar()
            assert markers == 0, "the node's writes must be rolled back"
        finally:
            await db.rollback()
            await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.id.in_(ids["s"])))
            await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(ids["r"])))
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["w"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["t"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
    await engine.dispose()


@pytest.mark.parametrize("mode", ["wait", "error"])
def test_cancel_during_step_is_not_overwritten(monkeypatch, mode):
    asyncio.run(_scenario(monkeypatch, mode))
