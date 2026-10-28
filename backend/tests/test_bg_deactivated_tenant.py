"""Background work never acts for a deactivated tenant (is_active = False): war-room listening, App Home,
workflow cron/event/in-flight runs, and the beat/queued jobs that would send or compute for a tenant.
Cleanup deletes ONLY rows whose ids were collected here."""
import asyncio
import secrets
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select, update

from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_triage import CaseTriageResult
from app.models.membership import TenantMembership
from app.models.notification import Notification
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.user import User
from app.models.war_room_suggestion import WarRoomMessageSeen, WarRoomSuggestion
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.utils.crypto import encrypt

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                   {"id": "ask", "type": "slack_ask_user", "config": {"email": "a@example.test", "message": "hi"}}],
         "edges": [{"id": "e1", "source": "start", "target": "ask", "source_handle": None}]}


class Ids:
    def __init__(self):
        self.h = secrets.token_hex(3)
        self.tenants, self.cases, self.users, self.wf, self.runs, self.channels = [], [], [], [], [], []


async def tenant(db, ids, *, active=True):
    t = Tenant(name="bgd-test", slug=f"bgd-{ids.h}-{len(ids.tenants)}", is_active=active)
    db.add(t)
    await db.flush()
    ids.tenants.append(int(t.id))
    return t


async def case(db, ids, t, **kw):
    c = Case(title="bgd-case", tenant_id=t.id, description="x", **kw)
    db.add(c)
    await db.flush()
    ids.cases.append(int(c.id))
    return c


async def wf(db, ids, t, trigger_type, trigger_filter=None):
    w = Workflow(tenant_id=t.id, name="bgd", enabled=True, trigger_type=trigger_type,
                 trigger_filter=trigger_filter, graph=GRAPH)
    db.add(w)
    await db.flush()
    ids.wf.append(int(w.id))
    return w


async def cleanup(ids):
    async with AsyncSessionLocal() as db:
        tids, cids, uids = ids.tenants or [-1], ids.cases or [-1], ids.users or [-1]
        run_ids = (await db.execute(select(WorkflowRun.id).where(WorkflowRun.workflow_id.in_(ids.wf or [-1])))).scalars().all()
        await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.run_id.in_(run_ids or [-1])))
        await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(run_ids or [-1])))
        await db.execute(delete(Workflow).where(Workflow.id.in_(ids.wf or [-1])))
        await db.execute(delete(WarRoomSuggestion).where(WarRoomSuggestion.case_id.in_(cids)))
        await db.execute(delete(WarRoomMessageSeen).where(WarRoomMessageSeen.slack_channel.in_(ids.channels or ["-"])))
        await db.execute(delete(Notification).where(Notification.case_id.in_(cids)))
        await db.execute(delete(CaseTriageResult).where(CaseTriageResult.case_id.in_(cids)))
        await db.execute(delete(SlackIntegration).where(SlackIntegration.tenant_id.in_(tids)))
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
        await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_(tids)))
        await db.execute(delete(Case).where(Case.id.in_(cids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
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


# --- war room -------------------------------------------------------------------------------------------

def _wr_event(ch):
    return {"channel": ch, "ts": f"{time.time():.6f}", "thread_ts": None, "user": "U1",
            "text": "seen 185.220.101.4 on WS-FIN-07, contained at 14:32"}


@scenario
async def test_war_room_task_noop_for_inactive_tenant(ids, monkeypatch):
    from app.tasks import war_room as wr
    calls = []

    async def fake_call(token, method, **kw):
        calls.append(method)
        return {"ok": True, "ts": "9.9", "user": {}}

    monkeypatch.setattr(wr, "slack_call", fake_call)
    async with AsyncSessionLocal() as db:
        t = await tenant(db, ids, active=False)
        ch = f"C{secrets.token_hex(4).upper()}"
        ids.channels.append(ch)
        c = await case(db, ids, t, slack_channel_id=ch, slack_listen=True)
        db.add(SlackIntegration(tenant_id=t.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y")))
        await db.commit()
        tid, cid = t.id, c.id
    await wr.process_message(tid, _wr_event(ch))
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(func.count()).select_from(WarRoomMessageSeen).where(
            WarRoomMessageSeen.slack_channel == ch))).scalar() == 0
        assert (await db.execute(select(func.count()).select_from(WarRoomSuggestion).where(
            WarRoomSuggestion.case_id == cid))).scalar() == 0
        assert (await db.execute(select(SlackIntegration.last_message_event_at).where(
            SlackIntegration.tenant_id == tid))).scalar() is None
    assert calls == []


# --- App Home -------------------------------------------------------------------------------------------

@scenario
async def test_home_view_inactive_tenant_publishes_no_case_data(ids, monkeypatch):
    from app.services import slack_bot
    published = []

    async def fake_call(token, method, **kw):
        published.append((method, kw))
        return {"ok": True}

    async def boom(*a, **k):
        raise AssertionError("user/case data must not be loaded for a deactivated tenant")

    monkeypatch.setattr(slack_bot, "slack_call", fake_call)
    monkeypatch.setattr(slack_bot, "linked_user", boom)
    async with AsyncSessionLocal() as db:
        t = await tenant(db, ids, active=False)
        await case(db, ids, t)
        db.add(SlackIntegration(tenant_id=t.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y")))
        await db.commit()
        tid = t.id
    await slack_bot.publish_home(tid, "U1")
    text = str(published)
    assert "bgd-case" not in text and "New case" not in text
    assert all(m == "views.publish" for m, _ in published)
    assert "deactivated" in text


@scenario
async def test_case_event_alert_noop_for_inactive_tenant(ids, monkeypatch):
    from app.services import slack_bot
    calls = []

    async def fake_call(token, method, **kw):
        calls.append(method)
        return {"ok": True, "ts": "1.1"}

    monkeypatch.setattr(slack_bot, "slack_call", fake_call)
    async with AsyncSessionLocal() as db:
        t = await tenant(db, ids, active=False)
        c = await case(db, ids, t)
        db.add(SlackIntegration(tenant_id=t.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y"),
                                alert_channel="C1", alert_min_severity="low"))
        await db.commit()
        tid, cid = t.id, c.id
    await slack_bot.on_case_event(tid, "case.created", cid, {})
    assert calls == []


# --- workflows ------------------------------------------------------------------------------------------

@scenario
async def test_cron_and_event_skip_inactive_tenant(ids, monkeypatch):
    import app.tasks.workflows as tw
    from app.workflows.runtime import dispatch_event, run_due_schedules
    advanced = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: advanced.append(rid))
    async with AsyncSessionLocal() as db:
        dead, live = await tenant(db, ids, active=False), await tenant(db, ids)
        w_dead_cron = await wf(db, ids, dead, "schedule", "* * * * *")
        w_live_cron = await wf(db, ids, live, "schedule", "* * * * *")
        w_dead_ev = await wf(db, ids, dead, "case.created")
        w_live_ev = await wf(db, ids, live, "case.created")
        await db.commit()
        dead_id, live_id = dead.id, live.id
        wids = {"dc": w_dead_cron.id, "lc": w_live_cron.id, "de": w_dead_ev.id, "le": w_live_ev.id}

    await run_due_schedules(datetime.now(timezone.utc))
    await dispatch_event(dead_id, "case.created", None, None, {}, 0)
    await dispatch_event(live_id, "case.created", None, None, {}, 0)
    async with AsyncSessionLocal() as db:
        by_wf = {r.workflow_id for r in (await db.execute(select(WorkflowRun).where(
            WorkflowRun.workflow_id.in_(wids.values())))).scalars().all()}
    assert wids["dc"] not in by_wf and wids["de"] not in by_wf
    assert wids["lc"] in by_wf and wids["le"] in by_wf


async def _inflight(ids, *, step_status="pending"):
    async with AsyncSessionLocal() as db:
        t = await tenant(db, ids)
        w = await wf(db, ids, t, "manual")
        run = WorkflowRun(tenant_id=t.id, workflow_id=w.id, workflow_version=1, status="running",
                          trigger_payload={}, graph_snapshot=GRAPH)
        db.add(run)
        await db.flush()
        db.add(WorkflowRunStep(run_id=run.id, node_id="start", status="succeeded", output={}))
        step = WorkflowRunStep(run_id=run.id, node_id="ask", status=step_status) if step_status else None
        if step:
            db.add(step)
        await db.commit()
        return t.id, run.id, (step.id if step else None)


async def _deactivate(tid):
    async with AsyncSessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == tid).values(is_active=False))
        await db.commit()


async def _state(run_id):
    async with AsyncSessionLocal() as db:
        r = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalars().one()
        steps = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))).scalars().all()
        return r, {s.node_id: s for s in steps}


@scenario
async def test_inflight_step_stops_when_tenant_deactivated(ids, monkeypatch):
    import app.tasks.workflows as tw
    from app.workflows import runtime
    from app.workflows.nodes import EXECUTORS
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: None)
    monkeypatch.setattr(tw.withdraw_messages_task, "delay", lambda t: None)
    ran = []

    async def ex(nctx, config):
        ran.append(1)
        return {}

    monkeypatch.setitem(EXECUTORS, "slack_ask_user", ex)
    tid, run_id, step_id = await _inflight(ids)
    await _deactivate(tid)
    await runtime.execute_step(step_id)
    run, steps = await _state(run_id)
    assert ran == []
    assert run.status == "cancelled" and run.error == "tenant deactivated"
    assert steps["ask"].status == "cancelled"


@scenario
async def test_inflight_advance_stops_when_tenant_deactivated(ids, monkeypatch):
    import app.tasks.workflows as tw
    from app.workflows import runtime
    queued = []
    monkeypatch.setattr(tw.execute_step_task, "delay", lambda sid: queued.append(sid))
    monkeypatch.setattr(tw.withdraw_messages_task, "delay", lambda t: None)
    tid, run_id, _ = await _inflight(ids, step_status=None)  # trigger done, "ask" is ready
    await _deactivate(tid)
    await runtime.advance_run(run_id)
    run, steps = await _state(run_id)
    assert queued == [] and "ask" not in steps
    assert run.status == "cancelled" and run.error == "tenant deactivated"


@scenario
async def test_active_tenant_run_still_executes(ids, monkeypatch):
    import app.tasks.workflows as tw
    from app.workflows import runtime
    from app.workflows.nodes import EXECUTORS
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: None)
    ran = []

    async def ex(nctx, config):
        ran.append(1)
        return {"ok": True}

    monkeypatch.setitem(EXECUTORS, "slack_ask_user", ex)
    _, run_id, step_id = await _inflight(ids)
    await runtime.execute_step(step_id)
    _, steps = await _state(run_id)
    assert ran == [1] and steps["ask"].status == "succeeded"


@scenario
async def test_expire_waits_skips_inactive_tenant(ids, monkeypatch):
    import app.tasks.workflows as tw
    from app.workflows import runtime
    advanced = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: advanced.append(rid))

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(runtime, "_update_slack_messages", noop)
    out = {}
    for name, active in (("dead", False), ("live", True)):
        async with AsyncSessionLocal() as db:
            t = await tenant(db, ids, active=active)
            w = await wf(db, ids, t, "manual")
            run = WorkflowRun(tenant_id=t.id, workflow_id=w.id, workflow_version=1, status="waiting",
                              trigger_payload={}, graph_snapshot=GRAPH)
            db.add(run)
            await db.flush()
            db.add(WorkflowRunStep(run_id=run.id, node_id="ask", status="waiting", wait_token="tok-" + ids.h + name,
                                   wait_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5), output={}))
            await db.commit()
            out[name] = run.id
    await runtime.expire_waits()
    assert out["live"] in advanced and out["dead"] not in advanced
    _, dead_steps = await _state(out["dead"])
    assert dead_steps["ask"].status == "waiting"


# --- other jobs -----------------------------------------------------------------------------------------

@scenario
async def test_sla_sweep_skips_inactive_tenant(ids, monkeypatch):
    from app.tasks import sla
    seen = []

    async def fake_check(db, tenant_id):
        seen.append(tenant_id)

    monkeypatch.setattr(sla, "_check_tenant", fake_check)
    async with AsyncSessionLocal() as db:
        dead, live = await tenant(db, ids, active=False), await tenant(db, ids)
        await db.commit()
        d, l = dead.id, live.id
    await sla._run()
    assert d not in seen and l in seen


@scenario
async def test_enrichment_skips_inactive_tenant(ids, monkeypatch):
    from app.tasks import enrichment as en
    from app.core.config import settings
    monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", True)
    ran = []

    async def fake_run(db, tenant_id, *a, **k):
        ran.append(tenant_id)

    monkeypatch.setattr(en, "run_enrichment", fake_run)
    async with AsyncSessionLocal() as db:
        dead, live = await tenant(db, ids, active=False), await tenant(db, ids)
        await db.commit()
        d, l = dead.id, live.id
    await en._entry(d, "ip", "8.8.8.8", None, True)
    await en._entry(l, "ip", "8.8.8.8", None, True)
    assert d not in ran and l in ran


@scenario
async def test_triage_skips_inactive_tenant(ids, monkeypatch):
    from app.services.ai_service import AIService
    from app.tasks import triage
    called = []

    async def fake_gen(self, payload):
        called.append(self.__dict__.get("tenant_id"))
        return {"severity": "low", "tags": [], "next_steps_text": "x"}

    monkeypatch.setattr(AIService, "generate_triage", fake_gen)
    async with AsyncSessionLocal() as db:
        t = await tenant(db, ids, active=False)
        c = await case(db, ids, t)
        tr = CaseTriageResult(case_id=c.id, tenant_id=t.id, status="pending")
        db.add(tr)
        await db.commit()
        trid = tr.id
    await triage._run_with_session(trid)
    assert called == []
    async with AsyncSessionLocal() as db:
        tr = (await db.execute(select(CaseTriageResult).where(CaseTriageResult.id == trid))).scalars().one()
        assert tr.status == "failed" and tr.proposed_severity is None


@scenario
async def test_external_notification_skipped_for_inactive_tenant(ids, monkeypatch):
    from app.notifications import delivery

    class R:
        async def set(self, *a, **k):
            return True

        async def delete(self, *a):
            return 1

    sent = []

    async def fake_deliver(db, n):
        sent.append(n.id)
        return "slack"

    monkeypatch.setattr(delivery, "_deliver", fake_deliver)
    nids = []
    async with AsyncSessionLocal() as db:
        u = User(email=f"bgd-{ids.h}@example.test", hashed_password="x", is_active=True)
        db.add(u)
        await db.flush()
        ids.users.append(u.id)
        for active in (False, True):
            t = await tenant(db, ids, active=active)
            c = await case(db, ids, t)
            n = Notification(tenant_id=t.id, user_id=u.id, case_id=c.id, type="mention", summary="hi")
            db.add(n)
            await db.flush()
            nids.append(n.id)
        await db.commit()
    async with AsyncSessionLocal() as db:
        await delivery.deliver_one(db, nids[0], R())
        await delivery.deliver_one(db, nids[1], R())
    assert sent == [nids[1]]


@scenario
async def test_ticket_sync_skips_inactive_tenant(ids, monkeypatch):
    from app.models.ticket_connection import TicketConnection
    from app.services import ticket_sync
    from app.tasks import ticketing
    synced = []

    async def fake_sync(db, conn):
        synced.append(conn.tenant_id)
        return {"created": 0, "synced": 0, "errors": []}
    monkeypatch.setattr(ticket_sync, "sync_tenant", fake_sync)
    async with AsyncSessionLocal() as db:
        dead = await tenant(db, ids, active=False)
        db.add(TicketConnection(tenant_id=dead.id, provider="jira", base_url="https://x.atlassian.net",
                                credentials_enc=ticket_sync.encrypt_credentials({}), settings={}))
        await db.commit()
        d = dead.id
    await engine.dispose()  # the sync task body runs its own loop: pooled connections must not cross loops
    await asyncio.to_thread(ticketing.sync_tickets_task.run)
    assert d not in synced
