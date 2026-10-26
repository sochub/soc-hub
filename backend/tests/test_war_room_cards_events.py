"""Task-completed, workflow-finished and containment war-room cards."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.case_task import CaseTask
from app.models.slack_integration import SlackIntegration
from app.models.tenant_secret import TenantSecret
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.services import slack_bot
from app.services.task_service import update_case_task
from app.utils.crypto import encrypt
from app.workflows.runtime import finalize_run
from tests.guest_fixtures import make_case, make_tenant, make_user, run

CHANNEL = "CWRE1"
SECRET = "s3cr3t-token-value-xyz"
GRAPH = {"nodes": [{"id": "n1", "type": "http_request",
                    "config": {"url": "https://example.com/x", "headers": {"Authorization": "Bearer {{ secrets.TKN }}"}}}],
         "edges": []}


def flat(x):
    return json.dumps(x, ensure_ascii=False)


@pytest.fixture
def delays(monkeypatch):
    from app.tasks.slack import slack_case_event_task
    calls = []
    monkeypatch.setattr(slack_case_event_task, "delay", lambda *a: calls.append(a))
    return calls


@pytest.fixture
def posts(monkeypatch):
    calls = []

    async def fake(token, method, **kw):
        calls.append((method, kw))
        return {"ok": True, "channel": "C1", "ts": "1.1"}

    monkeypatch.setattr(slack_bot, "slack_call", fake)
    return calls


async def _setup(db, ids, channel=CHANNEL):
    t = await make_tenant(db, ids)
    u = await make_user(db, ids)
    case = await make_case(db, ids, t, slack_channel_id=channel)
    db.add(SlackIntegration(tenant_id=t.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y"),
                            alert_channel="CALERT"))
    await db.commit()
    return t, u, case


def _war(posts):
    return [kw for m, kw in posts if m == "chat.postMessage" and kw["channel"] == CHANNEL]


def test_task_done_posts_progress_card(delays, posts):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            tasks = [CaseTask(case_id=case.id, tenant_id=t.id, title=f"T{i}", phase="containment",
                              status="done" if i < 2 else "todo") for i in range(5)]
            db.add_all(tasks)
            await db.commit()
            await update_case_task(db, case=case, task=tasks[2], data={"status": "done"}, user_id=u.id)
            await db.commit()
            tid, cid = t.id, case.id
        assert len(delays) == 1 and delays[0][1] == "task.completed"
        assert delays[0][3] == {"title": "T2", "phase": "containment", "done": 3, "total": 5, "user_id": u.id}
        await slack_bot.on_case_event(*delays[0])
        war = _war(posts)
        assert len(war) == 1 and "3 of 5 done" in flat(war) and "Containment" in flat(war)
        assert war[0]["attachments"][0]["color"] == "#36c5f0"
        assert not [1 for m, kw in posts if kw.get("channel") == "CALERT"]
    run(body)


def test_task_reopen_no_card(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            task = CaseTask(case_id=case.id, tenant_id=t.id, title="T", phase="recovery", status="done")
            db.add(task)
            await db.commit()
            await update_case_task(db, case=case, task=task, data={"status": "todo"}, user_id=u.id)
            await db.commit()
        assert delays == []
    run(body)


async def _wf_run(db, t, case, *, status="failed", error=None, is_dry_run=False, with_case=True, graph=GRAPH):
    wf = Workflow(tenant_id=t.id, name="Isolate host", trigger_type="manual", graph=graph)
    db.add(wf)
    await db.flush()
    r = WorkflowRun(tenant_id=t.id, workflow_id=wf.id, workflow_version=1, case_id=case.id if with_case else None,
                    status="running", graph_snapshot=graph, is_dry_run=is_dry_run)
    db.add(r)
    await db.flush()
    db.add(WorkflowRunStep(run_id=r.id, node_id="n1", status="succeeded",
                           output={"status": 200, "headers": {}, "body": {"leak": "BODYTEXT"}}))
    db.add(WorkflowRunStep(run_id=r.id, node_id="n2", status="failed" if status == "failed" else "succeeded", output={"x": 1}))
    await db.flush()
    await finalize_run(db, r, status, error)
    await db.commit()
    return r


def test_workflow_success_card_lists_steps(delays, posts):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            r = await _wf_run(db, t, case, status="succeeded")
            tid, cid = t.id, case.id
        assert len(delays) == 1 and delays[0][1] == "workflow.finished"
        p = delays[0][3]
        assert p["status"] == "succeeded" and p["workflow_name"] == "Isolate host" and p["error"] is None
        assert p["steps"][0] == {"node_id": "n1", "status": "succeeded", "http_status": 200}
        assert "BODYTEXT" not in flat(p)
        await slack_bot.on_case_event(*delays[0])
        war = _war(posts)
        assert len(war) == 1 and "n1" in flat(war) and "HTTP 200" in flat(war) and "Isolate host" in flat(war)
        assert war[0]["attachments"][0]["color"] == "#36c5f0"
    run(body)


def test_workflow_failed_card_redacts_and_truncates(delays, posts):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            db.add(TenantSecret(tenant_id=t.id, name="TKN", value_enc=encrypt(SECRET)))
            await db.commit()
            await _wf_run(db, t, case, status="failed", error=f"step 'n2' failed: bad {SECRET} " + "z" * 500)
        p = delays[0][3]
        assert SECRET not in flat(p) and len(p["error"]) <= 300 and "step 'n2' failed" in p["error"]
        await slack_bot.on_case_event(*delays[0])
        war = _war(posts)
        assert war[0]["attachments"][0]["color"] == "#e01e5a" and SECRET not in flat(war)
        assert f"/workflows/runs/{p['run_id']}|View run>" in flat(war)
    run(body)


def test_secret_straddling_300_not_leaked(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            db.add(TenantSecret(tenant_id=t.id, name="TKN", value_enc=encrypt(SECRET)))
            await db.commit()
            await _wf_run(db, t, case, status="failed", error="a" * 290 + SECRET)
        err = delays[0][3]["error"]
        assert len(err) <= 300 and SECRET[:5] not in err
    run(body)


def test_card_failure_does_not_break_finalize(delays, monkeypatch):
    from app.workflows import runtime

    async def boom(db, r):
        raise RuntimeError("x")

    monkeypatch.setattr(runtime, "_queue_finished_card", boom)

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            r = await _wf_run(db, t, case, status="succeeded")
            rid = r.id
        async with AsyncSessionLocal() as db:
            assert (await db.get(WorkflowRun, rid)).status == "succeeded"
        assert delays == []
    run(body)


def test_dry_run_no_card(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            await _wf_run(db, t, case, status="succeeded", is_dry_run=True)
        assert delays == []
    run(body)


def test_run_without_case_no_card(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            await _wf_run(db, t, case, status="succeeded", with_case=False)
        assert delays == []
    run(body)


def test_containment_card_mentions_recent_workflow(delays, posts):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids)
            await _wf_run(db, t, case, status="succeeded")
            tid, cid = t.id, case.id
        delays.clear()
        now = datetime.now(timezone.utc)
        await slack_bot.on_case_event(tid, "containment.confirmed", cid, {"at": now.isoformat(), "user_id": u.id})
        await slack_bot.on_case_event(tid, "containment.confirmed", cid,
                                      {"at": (now + timedelta(minutes=30)).isoformat(), "user_id": u.id})
        war = _war(posts)
        assert len(war) == 2
        assert "Containment confirmed" in flat(war[0]) and "after workflow *Isolate host* succeeded" in flat(war[0])
        assert war[0]["attachments"][0]["color"] == "#2eb67d"
        assert "after workflow" not in flat(war[1])
    run(body)


def test_no_war_room_no_post(delays, posts):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, u, case = await _setup(db, ids, channel=None)
            tid, cid = t.id, case.id
        for ev, ch in (("task.completed", {"title": "T", "phase": "recovery", "done": 1, "total": 2, "user_id": u.id}),
                       ("workflow.finished", {"status": "failed", "steps": [], "error": None}),
                       ("containment.confirmed", {"at": datetime.now(timezone.utc).isoformat(), "user_id": u.id})):
            await slack_bot.on_case_event(tid, ev, cid, ch)
        assert [1 for m, _ in posts if m == "chat.postMessage"] == []
    run(body)
