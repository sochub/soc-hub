"""New behavior gained by moving route logic into services (alert/IOC/task/playbook/run services)."""
import asyncio

from sqlalchemy import delete, select

from app.api import deps
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Alert
from app.models.membership import TenantMembership
from tests.guest_fixtures import Ids, cleanup, client, make_case, make_tenant, make_user


def _run(body):
    async def main():
        await engine.dispose()
        ids = Ids()
        ids.alerts = []
        try:
            async with AsyncSessionLocal() as db:
                await body(db, ids)
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Alert).where(Alert.id.in_(ids.alerts or [-1])))
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(ids.users or [-1])))
                await db.commit()
            await cleanup(ids)
            await engine.dispose()
    asyncio.run(main())


async def _setup(db, ids, monkeypatch):
    t = await make_tenant(db, ids)
    user = await make_user(db, ids)
    db.add(TenantMembership(user_id=user.id, tenant_id=t.id, role="analyst"))
    await db.commit()
    await db.refresh(user, ["memberships"])
    user._active_tenant_id = t.id
    monkeypatch.setattr(deps, "_active_role", lambda u: "analyst")
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: t.id
    app.dependency_overrides[deps.require_analyst_or_above] = lambda: user
    return t, user


async def _alert(db, ids, t, ext):
    a = Alert(tenant_id=t.id, source="s", external_id=f"{ids.h}-{ext}", title="a", severity="high",
              payload={"src_ip": "10.4.5.6"}, status="pending")
    db.add(a)
    await db.commit()
    ids.alerts.append(int(a.id))
    return a


async def _audit(db, tenant_id, entity_id, action):
    return (await db.execute(select(AuditLog).where(
        AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "alert", AuditLog.entity_id == entity_id,
        AuditLog.action == action))).scalars().all()


def test_dismiss_writes_audit_and_reason(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch)
        a = await _alert(db, ids, t, "d")
        b = await _alert(db, ids, t, "e")
        async with client() as c:
            r = await c.post(f"/api/v1/alerts/{a.id}/dismiss", json={"reason": "benign scanner"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "dismissed" and r.json()["dismiss_reason"] == "benign scanner"
            r = await c.post(f"/api/v1/alerts/{b.id}/dismiss")  # no body still works
            assert r.status_code == 200 and r.json()["dismiss_reason"] is None, r.text
            r = await c.post(f"/api/v1/alerts/{b.id}/dismiss", json={"reason": "x" * 1001})
            assert r.status_code == 422, r.text
        rows = await _audit(db, t.id, a.id, "alert_dismissed")
        assert len(rows) == 1 and rows[0].user_id == user.id and rows[0].changes.get("reason") == "benign scanner"
    _run(body)


def test_promote_writes_audit(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch)
        case = await make_case(db, ids, t)
        a = await _alert(db, ids, t, "p")
        async with client() as c:
            r = await c.post(f"/api/v1/alerts/{a.id}/promote/{case.id}")
            assert r.status_code == 200 and r.json()["status"] == "promoted", r.text
        rows = await _audit(db, t.id, a.id, "alert_promoted")
        assert len(rows) == 1 and rows[0].user_id == user.id and rows[0].changes.get("case_id") == case.id
    _run(body)


def test_case_update_owner_must_be_member(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch)
        t2 = await make_tenant(db, ids)
        outsider = await make_user(db, ids)
        db.add(TenantMembership(user_id=outsider.id, tenant_id=t2.id, role="analyst"))
        await db.commit()
        case = await make_case(db, ids, t)
        async with client() as c:
            r = await c.put(f"/api/v1/cases/{case.id}", json={"owner_id": outsider.id})
            assert r.status_code == 422, r.text
            r = await c.put(f"/api/v1/cases/{case.id}", json={"owner_id": user.id})
            assert r.status_code == 200 and r.json()["owner_id"] == user.id, r.text
    _run(body)


# ---- regression coverage for the moved logic (behavior unchanged)

def test_task_update_and_apply_playbook_go_through_services(monkeypatch):
    from app.models.case import TimelineEvent
    from app.models.case_task import CaseTask
    from app.models.playbook import PlaybookTaskTemplate, PlaybookTemplate

    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch)
        case = await make_case(db, ids, t)
        tpl = PlaybookTemplate(tenant_id=t.id, name=f"pb-{ids.h}")
        db.add(tpl)
        await db.flush()
        db.add_all([PlaybookTaskTemplate(template_id=tpl.id, phase="containment", title="Isolate host", order=1),
                    PlaybookTaskTemplate(template_id=tpl.id, phase="eradication", title="Remove implant", order=2)])
        await db.commit()
        async with client() as c:
            r = await c.post(f"/api/v1/cases/{case.id}/apply-playbook/{tpl.id}")
            assert r.status_code == 200 and len(r.json()) == 2, r.text
            task_id = r.json()[0]["id"]
            r = await c.put(f"/api/v1/cases/{case.id}/tasks/{task_id}", json={"status": "done"})
            assert r.status_code == 200 and r.json()["status"] == "done", r.text
            r = await c.put(f"/api/v1/cases/{case.id}/tasks/{task_id}", json={"status": "done"})  # no-op
            assert r.status_code == 200, r.text
            assert (await c.post(f"/api/v1/cases/{case.id}/apply-playbook/999999999")).status_code == 404
        uid, cid = user.id, case.id
        db.expire_all()
        task = (await db.execute(select(CaseTask).where(CaseTask.id == task_id))).scalars().one()
        assert task.completed_by == uid and task.completed_at is not None
        events = (await db.execute(select(TimelineEvent.content).where(
            TimelineEvent.case_id == cid, TimelineEvent.event_type == "task"))).scalars().all()
        assert any(e.startswith("Playbook applied:") for e in events), events
        assert sum(e.startswith("Task completed:") for e in events) == 1, events
    _run(body)


def test_copilot_apply_playbook_adds_timeline_entry(monkeypatch):
    from app.models.case import TimelineEvent
    from app.models.playbook import PlaybookTaskTemplate, PlaybookTemplate
    from app.services import task_service

    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch)
        case = await make_case(db, ids, t)
        tpl = PlaybookTemplate(tenant_id=t.id, name=f"pb-{ids.h}")
        db.add(tpl)
        await db.flush()
        db.add(PlaybookTaskTemplate(template_id=tpl.id, phase="containment", title="Isolate host", order=1))
        await db.commit()
        async with client() as c:
            r = await c.post("/api/v1/copilot/actions/execute",
                             json={"type": "apply_playbook", "case_id": case.id, "params": {"template_id": tpl.id}})
            assert r.status_code == 200 and r.json()["ok"] is True, r.text
        events = (await db.execute(select(TimelineEvent.content).where(
            TimelineEvent.case_id == case.id, TimelineEvent.event_type == "task"))).scalars().all()
        assert events == [f"Playbook applied: **pb-{ids.h}** (1 tasks added)"], events
        assert await task_service.apply_playbook(db, case=case, template_id=tpl.id, tenant_id=t.id,
                                                 user_id=user.id) == 0  # dedupe by phase+title
        await db.rollback()
    _run(body)


def test_enrichment_view_and_manual_run_guards(monkeypatch):
    from app.enrichment.view import enrichment_view
    from app.models.ioc import IOC
    from app.models.workflow import Workflow
    from app.workflows.runtime import start_manual_run

    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch)
        t2 = await make_tenant(db, ids)
        case = await make_case(db, ids, t)
        ioc = IOC(tenant_id=t.id, ioc_type="ip_address", value="8.8.4.4", tlp="green")
        wf = Workflow(tenant_id=t.id, name="wf", enabled=False, trigger_type="manual",
                      graph={"nodes": [{"id": "t", "type": "trigger"}], "edges": []})
        db.add_all([ioc, wf])
        await db.commit()
        ioc_id, wf_id = ioc.id, wf.id
        try:
            view = await enrichment_view(db, kind="ioc", obj_id=ioc.id, tenant_id=t.id)
            assert view["enrichable"] is True and view["effective_tlp"] == "green" and view["results"] == []
            assert await enrichment_view(db, kind="ioc", obj_id=ioc.id, tenant_id=t2.id) is None
            assert await enrichment_view(db, kind="bogus", obj_id=ioc.id, tenant_id=t.id) is None
            try:
                await start_manual_run(db, wf=wf, case=case, user_id=user.id)
                raise AssertionError("disabled workflow must not run")
            except ValueError as e:
                assert str(e) == "not_runnable"
            wf.enabled = True
            run = await start_manual_run(db, wf=wf, case=case, user_id=user.id)
            assert run.case_id == case.id and run.trigger_payload["event"] == "manual"
            await db.rollback()
        finally:
            await db.rollback()
            await db.execute(delete(IOC).where(IOC.id == ioc_id))
            await db.execute(delete(Workflow).where(Workflow.id == wf_id))
            await db.commit()
    _run(body)
