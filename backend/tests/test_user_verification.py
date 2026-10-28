"""email_ask_user + the public /user-verification answer page: send, wait, GET records nothing, POST answers
once and resumes the run, expiry, Slack-question tokens refused, deactivated tenants refused, validation, and the
packaged templates' branches."""
import asyncio
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.services import email_service
from app.workflows.nodes import WAIT, NodeContext, NodeError
from app.workflows.nodes.email_ask import run_email_ask_user
from app.workflows.templating import eval_expr

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "workflows" / "templates"


async def _scenario(monkeypatch):
    import app.tasks.workflows as tw
    await engine.dispose()
    sent, advanced = [], []
    monkeypatch.setattr(email_service, "_smtp_configured", lambda: True)
    monkeypatch.setattr(email_service, "send_user_verification_email",
                        lambda to, **kw: sent.append((to, kw)) or True)
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: advanced.append(rid))
    h = secrets.token_hex(3)
    ids = {"r": [], "s": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name=f"Acme {h}", slug=f"uv-{h}")
        db.add(t)
        await db.flush()
        wf = Workflow(tenant_id=t.id, name="uv", trigger_type="manual", graph={"nodes": [], "edges": []})
        db.add(wf)
        await db.commit()
        tid, wid = t.id, wf.id

    async def ask(config):
        async with AsyncSessionLocal() as db:
            run = WorkflowRun(tenant_id=tid, workflow_id=wid, workflow_version=1, status="running", graph_snapshot={})
            db.add(run)
            await db.flush()
            step = WorkflowRunStep(run_id=run.id, node_id="ask", status="running")
            db.add(step)
            await db.flush()
            ids["r"].append(run.id)
            ids["s"].append(step.id)
            out = await run_email_ask_user(NodeContext(db=db, run=run, step=step, node={}, ctx={}), config)
            assert out is WAIT
            step.status = "waiting"
            await db.commit()
            return step.id, step.wait_token

    async def fresh(sid):
        async with AsyncSessionLocal() as db:
            return await db.get(WorkflowRunStep, sid)

    try:
        sid, tok = await ask({"email": "bob@acme.com", "message": "Login from Lagos <b>bold</b>"})
        to, kw = sent[0]
        assert to == "bob@acme.com" and kw["org_name"] == f"Acme {h}" and kw["link"].endswith(f"/verify/{tok}")
        assert kw["hours"] == 1.0 and len(tok) >= 40
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            q = (await c.get(f"/api/v1/user-verification/{tok}")).json()
            assert q["state"] == "open" and q["buttons"] == ["It was me", "It wasn't me"]
            assert q["organization"] == f"Acme {h}" and q["message"].startswith("Login from Lagos")
            assert (await fresh(sid)).status == "waiting"  # opening the page answers nothing
            assert (await c.post(f"/api/v1/user-verification/{tok}", json={"index": 3})).status_code == 422
            r = await c.post(f"/api/v1/user-verification/{tok}", json={"index": 1})
            assert r.json() == {"state": "answered", "response": "It wasn't me"}, r.text
            st = await fresh(sid)
            assert st.status == "succeeded" and st.output["responder_email"] == "bob@acme.com" and not st.output["timed_out"]
            assert advanced == [st.run_id]
            assert (await c.post(f"/api/v1/user-verification/{tok}", json={"index": 0})).status_code == 410
            assert (await c.get(f"/api/v1/user-verification/{tok}")).json() == {"state": "closed"}
            assert (await c.get("/api/v1/user-verification/nope-nope-nope-nope-nope")).json() == {"state": "closed"}

            # expired before the sweeper ran
            sid2, tok2 = await ask({"email": "amy@acme.com", "message": "x", "buttons": ["Ok"], "timeout_hours": 2})
            async with AsyncSessionLocal() as db:
                s2 = await db.get(WorkflowRunStep, sid2)
                s2.wait_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                await db.commit()
            assert (await c.post(f"/api/v1/user-verification/{tok2}", json={"index": 0})).status_code == 410

            # a Slack question's token can't be answered here
            async with AsyncSessionLocal() as db:
                run = WorkflowRun(tenant_id=tid, workflow_id=wid, workflow_version=1, status="waiting", graph_snapshot={})
                db.add(run)
                await db.flush()
                slack_tok = secrets.token_urlsafe(32)
                s3 = WorkflowRunStep(run_id=run.id, node_id="ask", status="waiting", wait_token=slack_tok,
                                     output={"target_slack_id": "U1", "buttons": ["Yes", "No"]})
                db.add(s3)
                await db.commit()
                ids["r"].append(run.id)
                ids["s"].append(s3.id)
            assert (await c.post(f"/api/v1/user-verification/{slack_tok}", json={"index": 0})).status_code == 410

            # a deactivated tenant's questions close
            sid4, tok4 = await ask({"email": "cat@acme.com", "message": "x"})
            async with AsyncSessionLocal() as db:
                (await db.get(Tenant, tid)).is_active = False
                await db.commit()
            assert (await c.get(f"/api/v1/user-verification/{tok4}")).json() == {"state": "closed"}
            async with AsyncSessionLocal() as db:
                (await db.get(Tenant, tid)).is_active = True
                await db.commit()

        for bad, msg in (({"email": "not-an-email", "message": "x"}, "valid"), ({"email": "a@b.co", "message": " "}, "empty"),
                         ({"email": "a@b.co", "message": "x", "timeout_hours": 200}, "timeout")):
            with pytest.raises(NodeError, match=msg):
                await run_email_ask_user(NodeContext(db=None, run=SimpleNamespace(tenant_id=tid), step=None, node={}, ctx={}), bad)
        monkeypatch.setattr(email_service, "_smtp_configured", lambda: False)
        with pytest.raises(NodeError, match="SMTP"):
            await run_email_ask_user(NodeContext(db=None, run=SimpleNamespace(tenant_id=tid), step=None, node={}, ctx={}),
                                     {"email": "a@b.co", "message": "x"})
        async with AsyncSessionLocal() as db:
            assert "user_answered" in set((await db.execute(select(AuditLog.action).where(AuditLog.tenant_id == tid))).scalars())
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.id.in_(ids["s"] or [-1])))
            await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(ids["r"] or [-1])))
            await db.execute(delete(Workflow).where(Workflow.id == wid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()


def test_email_ask_user_and_answer_page(monkeypatch):
    asyncio.run(_scenario(monkeypatch))


@pytest.mark.parametrize("key", ["user-verification-email", "user-verification-slack"])
def test_verification_templates_branch_on_the_answer(key):
    d = json.loads((TEMPLATES / f"{key}.json").read_text())
    nodes = {n["id"]: n for n in d["graph"]["nodes"]}
    yes, no = nodes["ask"]["config"]["buttons"]
    expr = nodes["confirmed"]["config"]["expression"]
    assert eval_expr(expr, {"steps": {"ask": {"output": {"response": yes, "timed_out": False}}}}) is True
    for out in ({"response": no, "timed_out": False}, {"response": None, "timed_out": True}):
        assert eval_expr(expr, {"steps": {"ask": {"output": out}}}) is False  # no, or silence, escalates
    edges = {(e["source"], e.get("source_handle")): e["target"] for e in d["graph"]["edges"]}
    assert edges[("confirmed", "true")] == "dismiss" and edges[("confirmed", "false")] == "promote"
