import asyncio
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.utils.crypto import encrypt
from app.workflows import runtime
from app.services.slack_service import SlackError


async def _scenario(monkeypatch):
    h = secrets.token_hex(3)
    ids = {"t": [], "w": [], "r": [], "s": []}
    calls, advanced = [], []
    fail = {"exc": None}

    async def fake_call(token, method, **kw):
        calls.append((method, kw))
        if fail["exc"]:
            raise fail["exc"]
        return {}

    import app.services.slack_service as ss
    import app.tasks.workflows as tw
    monkeypatch.setattr(ss, "slack_call", fake_call)
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: advanced.append(rid))

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}")
            db.add(t)
            await db.flush()
            ids["t"] = [t.id]
            db.add(SlackIntegration(tenant_id=t.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y")))
            wf = Workflow(tenant_id=t.id, name="wf-test", trigger_type="manual", graph={"nodes": [], "edges": []})
            db.add(wf)
            await db.flush()
            ids["w"] = [wf.id]
            tid, wid = t.id, wf.id

            async def mk(expires, token=True, ts="1.2"):
                run = WorkflowRun(tenant_id=tid, workflow_id=wid, workflow_version=1, status="waiting", graph_snapshot={})
                db.add(run)
                await db.flush()
                ids["r"].append(run.id)
                run_id = run.id
                step = WorkflowRunStep(run_id=run_id, node_id="n1", status="waiting",
                                       wait_token=secrets.token_hex(8) if token else None, wait_expires_at=expires,
                                       output={"target_slack_id": "U1", "channel": "D1", "message_ts": ts, "buttons": ["Yes", "No"]})
                db.add(step)
                await db.flush()
                ids["s"].append(step.id)
                sid = step.id
                await db.commit()
                return run_id, sid

            async def fresh(sid):
                db.expire_all()
                return (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == sid))).scalars().one()

            r_exp, s_exp = await mk(now - timedelta(minutes=5), ts="9.9")
            r_fut, s_fut = await mk(now + timedelta(hours=1))
            r_loop, s_loop = await mk(None, token=False)

            fail["exc"] = RuntimeError("slack down")  # advance must still be recorded
            await runtime.expire_waits()
            fail["exc"] = None
            st = await fresh(s_exp)
            assert st.status == "succeeded" and st.output["timed_out"] is True and st.output["response"] is None
            assert st.finished_at is not None
            assert [c for c in calls if c[0] == "chat.update" and c[1]["ts"] == "9.9" and "expired" in c[1]["text"]]
            assert r_exp in advanced
            for sid, rid in ((s_fut, r_fut), (s_loop, r_loop)):
                assert (await fresh(sid)).status == "waiting" and rid not in advanced

            # cancel_run: no Slack I/O, targets queued; flush after commit dispatches the task
            withdrawn = []
            monkeypatch.setattr(tw.withdraw_messages_task, "delay", lambda targets: withdrawn.append(targets))
            calls.clear()
            rid, sid = await mk(now + timedelta(hours=1), ts="3.4")
            run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == rid))).scalars().one()
            await runtime.cancel_run(db, run)
            assert calls == [] and withdrawn == []
            assert db.sync_session.info["slack_withdraw"] == [[tid, "D1", "3.4"]]
            await db.commit()
            runtime.flush_withdrawals(db)
            assert withdrawn == [[[tid, "D1", "3.4"]]] and "slack_withdraw" not in db.sync_session.info
            runtime.flush_withdrawals(db)
            assert len(withdrawn) == 1
            assert (await fresh(sid)).status == "cancelled"

            # task body: "withdrawn" edit attempted; failures (SlackError or anything) never raise
            for exc in (None, SlackError("boom"), RuntimeError("boom")):
                calls.clear()
                fail["exc"] = exc
                await runtime.withdraw_messages([[tid, "D1", "3.4"]])
                assert [c for c in calls if c[0] == "chat.update" and c[1]["ts"] == "3.4" and "withdrawn" in c[1]["text"]]
        finally:
            await db.rollback()
            if ids["s"]:
                await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.id.in_(ids["s"])))
            if ids["r"]:
                await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(ids["r"])))
            if ids["w"]:
                await db.execute(delete(Workflow).where(Workflow.id.in_(ids["w"])))
            if ids["t"]:
                await db.execute(delete(SlackIntegration).where(SlackIntegration.tenant_id.in_(ids["t"])))
                await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
        n = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
        assert n == 0
    await engine.dispose()


def test_expire_waits_and_cancel(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
