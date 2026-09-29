import asyncio
import secrets

from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.services import slack_actions as sa
from app.services.slack_actions import authorize_answer
from app.utils.crypto import encrypt


def test_authorize_answer_target_user_while_waiting():
    assert authorize_answer("waiting", "U1", "U1") is None


def test_authorize_answer_wrong_user():
    assert "isn't for you" in authorize_answer("waiting", "U1", "U2")


def test_authorize_answer_already_answered_or_expired():
    for status in ("succeeded", "cancelled", "failed"):
        assert "already" in authorize_answer(status, "U1", "U1")


def test_authorize_answer_missing_target():
    assert authorize_answer("waiting", None, "U1") is not None


async def _scenario(monkeypatch):
    h = secrets.token_hex(3)
    ids = {"t": [], "w": [], "r": [], "s": []}
    ephem, updates, advanced = [], [], []

    async def fake_call(token, method, **kw):
        if method == "users.info":
            return {"user": {"profile": {"email": "bob@example.test"}}}
        updates.append((method, kw))
        return {}

    async def fake_ephemeral(payload, text):
        ephem.append(text)

    import app.tasks.workflows as tw
    monkeypatch.setattr(sa, "slack_call", fake_call)
    monkeypatch.setattr(sa, "_ephemeral", fake_ephemeral)
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: advanced.append(rid))

    def payload(user="U1"):
        return {"user": {"id": user}, "response_url": "https://hooks.slack.com/x"}

    async with AsyncSessionLocal() as db:
        try:
            t1 = Tenant(name="wf-test", slug=f"wf-test-{h}-a")
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-b")
            db.add_all([t1, t2])
            await db.flush()
            ids["t"] = [t1.id, t2.id]
            db.add(SlackIntegration(tenant_id=t1.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y")))
            wf = Workflow(tenant_id=t1.id, name="wf-test", trigger_type="manual", graph={"nodes": [], "edges": []})
            db.add(wf)
            await db.flush()
            ids["w"] = [wf.id]
            tid, t2id, wid = t1.id, t2.id, wf.id

            async def mk(run_status="waiting", step_status="waiting"):
                run = WorkflowRun(tenant_id=tid, workflow_id=wid, workflow_version=1, status=run_status, graph_snapshot={})
                db.add(run)
                await db.flush()
                ids["r"].append(run.id)
                run_id = run.id
                tok = secrets.token_hex(8)
                step = WorkflowRunStep(run_id=run_id, node_id="n1", status=step_status, wait_token=tok,
                                       output={"target_slack_id": "U1", "channel": "D1", "message_ts": "1.2", "buttons": ["Yes", "No"]})
                db.add(step)
                await db.flush()
                sid = step.id
                await db.commit()
                ids["s"].append(sid)
                return sid, tok

            async def fresh(sid):
                db.expire_all()
                return (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == sid))).scalars().one()

            # happy path
            sid, tok = await mk()
            await sa.handle_answer(tid, payload(), tok, 1)
            st = await fresh(sid)
            assert st.status == "succeeded" and st.output["response"] == "No"
            assert st.output["responder_slack_id"] == "U1" and st.output["responder_email"] == "bob@example.test"
            assert st.output["timed_out"] is False and st.finished_at is not None
            assert updates and updates[0][0] == "chat.update" and updates[0][1]["channel"] == "D1" and updates[0][1]["ts"] == "1.2"
            assert advanced == [st.run_id]

            # second click
            await sa.handle_answer(tid, payload(), tok, 0)
            assert "already" in ephem[-1] and len(advanced) == 1
            assert (await fresh(sid)).output["response"] == "No"

            # wrong user
            sid2, tok2 = await mk()
            await sa.handle_answer(tid, payload("U2"), tok2, 0)
            assert "isn't for you" in ephem[-1] and (await fresh(sid2)).status == "waiting" and len(advanced) == 1

            # out of range
            await sa.handle_answer(tid, payload(), tok2, 5)
            assert "Unknown option" in ephem[-1] and (await fresh(sid2)).status == "waiting"

            # other tenant
            await sa.handle_answer(t2id, payload(), tok2, 0)
            assert "already answered" in ephem[-1] and (await fresh(sid2)).status == "waiting"

            # cancelled
            sid3, tok3 = await mk("cancelled", "cancelled")
            await sa.handle_answer(tid, payload(), tok3, 0)
            assert "already" in ephem[-1] and len(advanced) == 1

            # Slack not configured -> ephemeral, not accepted
            await db.execute(delete(SlackIntegration).where(SlackIntegration.tenant_id == tid))
            await db.commit()
            await sa.handle_answer(tid, payload(), tok2, 0)
            assert "not recorded" in ephem[-1] and (await fresh(sid2)).status == "waiting" and len(advanced) == 1
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


def test_handle_answer(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
