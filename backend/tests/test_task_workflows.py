"""Playbook tasks that link a manual workflow: link validation, the link carried onto a case when the playbook is
applied, running it from the task (run recorded on the task, timeline + audit), disabled/missing workflows, roles,
ad-hoc tasks, unlinking, and the MCP tool."""
import asyncio
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_task import CaseTask
from app.models.playbook import PlaybookTemplate
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                   {"id": "note", "type": "case_add_note", "config": {"content": "hi"}}],
         "edges": [{"id": "e1", "source": "start", "target": "note", "source_handle": None}]}


async def _scenario(monkeypatch):
    import app.tasks.workflows as tw
    await engine.dispose()
    queued = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: queued.append(rid))
    h = secrets.token_hex(3)
    role = {"v": "admin"}
    async with AsyncSessionLocal() as db:
        t, t2 = Tenant(name="tw", slug=f"tw-{h}-a"), Tenant(name="tw", slug=f"tw-{h}-b")
        user = User(email=f"tw-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, t2, user])
        await db.flush()
        contain = Workflow(tenant_id=t.id, name="Contain host", trigger_type="manual", enabled=True, graph=GRAPH)
        off = Workflow(tenant_id=t.id, name="Disabled one", trigger_type="manual", enabled=False, graph=GRAPH)
        auto = Workflow(tenant_id=t.id, name="On create", trigger_type="case.created", enabled=True, graph=GRAPH)
        foreign = Workflow(tenant_id=t2.id, name="Theirs", trigger_type="manual", enabled=True, graph=GRAPH)
        case = Case(title="incident", tenant_id=t.id)
        db.add_all([contain, off, auto, foreign, case])
        await db.commit()
        tid, t2id, uid, cid = t.id, t2.id, user.id, case.id
        w_ok, w_off, w_auto, w_foreign = contain.id, off.id, auto.id, foreign.id

    def deny():
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
    app.dependency_overrides[deps.require_admin] = lambda: user if role["v"] == "admin" else deny()
    app.dependency_overrides[deps.require_analyst_or_above] = lambda: user if role["v"] != "viewer" else deny()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            def pb(wf):
                return {"name": f"Ransomware {h}", "tasks": [
                    {"phase": "containment", "title": "Isolate the host", "workflow_id": wf},
                    {"phase": "identification", "title": "Scope the blast radius"}]}
            for bad in (w_auto, w_foreign, 999999999):  # not manual / another tenant's / missing
                assert (await c.post("/api/v1/playbooks/", json=pb(bad))).status_code == 422
            r = await c.post("/api/v1/playbooks/", json=pb(w_ok))
            assert r.status_code == 200, r.text
            pid = r.json()["id"]
            assert r.json()["tasks"][0]["workflow_id"] == w_ok
            role["v"] = "analyst"
            tasks = (await c.post(f"/api/v1/cases/{cid}/apply-playbook/{pid}")).json()
            iso = next(x for x in tasks if x["title"] == "Isolate the host")
            assert iso["workflow_id"] == w_ok and iso["workflow_name"] == "Contain host" and iso["workflow_runnable"]
            plain = next(x for x in tasks if x["title"] == "Scope the blast radius")
            assert plain["workflow_id"] is None

            r = await c.post(f"/api/v1/cases/{cid}/tasks/{iso['id']}/run")
            assert r.status_code == 200, r.text
            run_id = r.json()["run_id"]
            assert queued == [run_id]
            listed = {x["id"]: x for x in (await c.get(f"/api/v1/cases/{cid}/tasks")).json()}
            assert listed[iso["id"]]["last_run_id"] == run_id and listed[iso["id"]]["last_run_status"]
            assert (await c.post(f"/api/v1/cases/{cid}/tasks/{plain['id']}/run")).status_code == 409

            # ad-hoc task linked to a disabled workflow: listed, not runnable, run refused with the reason
            r = await c.post(f"/api/v1/cases/{cid}/tasks", json={"title": "Try the other one", "workflow_id": w_off})
            assert r.status_code == 201 and r.json()["workflow_runnable"] is False, r.text
            r2 = await c.post(f"/api/v1/cases/{cid}/tasks/{r.json()['id']}/run")
            assert r2.status_code == 409 and "disabled" in r2.json()["detail"]
            # unlink, and can't link another tenant's workflow
            assert (await c.put(f"/api/v1/cases/{cid}/tasks/{r.json()['id']}", json={"workflow_id": w_foreign})).status_code == 422
            u = await c.put(f"/api/v1/cases/{cid}/tasks/{r.json()['id']}", json={"workflow_id": None})
            assert u.status_code == 200 and u.json()["workflow_id"] is None
            role["v"] = "viewer"
            assert (await c.post(f"/api/v1/cases/{cid}/tasks/{iso['id']}/run")).status_code == 403

        async with AsyncSessionLocal() as db:
            run = await db.get(WorkflowRun, run_id)
            assert run.case_id == cid and run.workflow_id == w_ok
            notes = (await db.execute(select(TimelineEvent.content).where(TimelineEvent.case_id == cid))).scalars().all()
            assert any("Ran **Contain host** for task **Isolate the host**" in n for n in notes)
            acts = set((await db.execute(select(AuditLog.action).where(AuditLog.tenant_id == tid))).scalars())
            assert {"task_workflow_run", "task_workflow_linked"} <= acts, acts
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            runs = select(WorkflowRun.id).where(WorkflowRun.tenant_id == tid)
            await db.execute(delete(CaseTask).where(CaseTask.case_id == cid))
            await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.run_id.in_(runs)))
            await db.execute(delete(WorkflowRun).where(WorkflowRun.tenant_id == tid))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([tid, t2id])))
            await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == cid))
            await db.execute(delete(Case).where(Case.id == cid))
            await db.execute(delete(PlaybookTemplate).where(PlaybookTemplate.tenant_id == tid))
            await db.execute(delete(Workflow).where(Workflow.tenant_id.in_([tid, t2id])))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id.in_([tid, t2id])))
            await db.commit()
        await engine.dispose()


def test_task_workflows(monkeypatch):
    asyncio.run(_scenario(monkeypatch))


def test_mcp_run_case_task_workflow(monkeypatch):
    import pytest
    import app.tasks.workflows as tw
    from app.mcp import tools
    from app.mcp.tools import ToolError
    from tests.guest_fixtures import make_case, make_tenant
    from tests.test_mcp_tools import _member, _p, _run
    queued = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: queued.append(rid))
    monkeypatch.setattr(tools.advance_run_task, "delay", lambda rid: queued.append(rid))

    async def body(db, ids):
        t, t2 = await make_tenant(db, ids), await make_tenant(db, ids)
        user, other = await _member(db, ids, t), await _member(db, ids, t2)
        case = await make_case(db, ids, t)
        wf = Workflow(tenant_id=t.id, name="Contain", trigger_type="manual", enabled=True, graph=GRAPH)
        db.add(wf)
        await db.flush()
        task = CaseTask(case_id=case.id, tenant_id=t.id, title="Isolate", workflow_id=wf.id)
        db.add(task)
        await db.commit()
        cid, task_id, wf_id = case.id, task.id, wf.id
        listed = await tools.list_case_tasks(db, _p(user, t), case_id=cid)
        assert listed["tasks"][0]["workflow_name"] == "Contain" and listed["tasks"][0]["workflow_runnable"]
        r = await tools.run_case_task_workflow(db, _p(user, t), case_id=cid, task_id=task_id)
        assert queued == [r["run_id"]]
        with pytest.raises(ToolError):
            await tools.run_case_task_workflow(db, _p(other, t2), case_id=cid, task_id=task_id)
        await db.execute(delete(CaseTask).where(CaseTask.id == task_id))
        await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.run_id == r["run_id"]))
        await db.execute(delete(WorkflowRun).where(WorkflowRun.id == r["run_id"]))
        await db.execute(delete(Workflow).where(Workflow.id == wf_id))
        await db.commit()
    _run(body)
