import asyncio
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep

GOOD = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                   {"id": "note", "type": "case_add_note", "config": {"content": "hi"}}],
        "edges": [{"id": "e1", "source": "start", "target": "note", "source_handle": None}]}
BAD = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                 {"id": "note", "type": "case_add_note", "config": {}}], "edges": []}


def _wf(graph=GOOD, trigger="case.created", trigger_filter=None):
    return {"name": "t", "trigger_type": trigger, "trigger_filter": trigger_filter, "graph": graph}


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(delayed, ticks):
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": [], "workflows": []}
    state = {"tenant": None, "role": "admin", "user": None}
    async with AsyncSessionLocal() as db:
        try:
            ta = Tenant(name="wf-test", slug=f"wf-test-{h}-a")
            tb = Tenant(name="wf-test", slug=f"wf-test-{h}-b")
            user = User(email=f"wf-api-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([ta, tb, user])
            await db.flush()
            ids["tenants"] += [ta.id, tb.id]
            ids["users"].append(user.id)
            case1 = Case(title="wf-api-test", tenant_id=1)
            db.add(case1)
            wf_b = Workflow(tenant_id=tb.id, name="other", trigger_type="manual", graph=GOOD, enabled=False, version=1)
            db.add(wf_b)
            await db.flush()
            ids["cases"].append(case1.id)
            ids["workflows"].append(wf_b.id)
            await db.commit()
            state["tenant"], state["user"] = ta.id, user
            other_wf, other_case = wf_b.id, case1.id

            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_admin] = lambda: state["user"] if state["role"] == "admin" else _deny()
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: state["user"] if state["role"] != "viewer" else _deny()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.get("/api/v1/workflows/settings/http-allowlist")
                assert (r.status_code, r.json()) == (200, {"hosts": []}), "a"

                r = await c.post("/api/v1/workflows/", json=_wf())
                assert r.status_code == 201, "b"
                wf = r.json()
                ids["workflows"].append(wf["id"])
                assert wf["enabled"] is False and wf["validation_errors"] == []
                wid = f"/api/v1/workflows/{wf['id']}"

                r = await c.put(wid, json=_wf())
                assert r.status_code == 200 and r.json()["version"] == 2, f"c put {r.status_code}"
                r = await c.post(wid + "/enable")
                assert r.status_code == 200 and r.json()["enabled"] is True, f"c enable {r.status_code}"

                r = await c.put(wid, json=_wf(BAD))
                assert r.status_code == 422, "d"
                r = await c.post(wid + "/disable")
                assert r.status_code == 200 and r.json()["enabled"] is False, f"c disable {r.status_code}"

                r = await c.post("/api/v1/workflows/", json=_wf(BAD))
                bad_id = r.json()["id"]
                ids["workflows"].append(bad_id)
                r = await c.post(f"/api/v1/workflows/{bad_id}/enable")
                assert r.status_code == 422 and r.json()["detail"]["errors"], "e"

                # a broken trigger filter is a validation error like a broken graph
                r = await c.post("/api/v1/workflows/", json=_wf(trigger_filter="case.id =="))
                bad_filter_id = r.json()["id"]
                ids["workflows"].append(bad_filter_id)
                errs = r.json()["validation_errors"]
                assert errs and errs[0]["node_id"] is None and errs[0]["message"].startswith("trigger filter:"), errs
                r = await c.post(f"/api/v1/workflows/{bad_filter_id}/enable")
                assert r.status_code == 422 and r.json()["detail"]["errors"], "filter enable"
                r = await c.post(f"/api/v1/workflows/{bad_filter_id}/dry-run", json={})
                assert r.status_code == 422, "filter dry-run"
                assert (await c.post(f"/api/v1/workflows/{bad_filter_id}/validate")).json()["errors"], "filter validate"
                r = await c.post(wid + "/enable")
                assert r.status_code == 200, "re-enable"
                r = await c.put(wid, json=_wf(trigger_filter="case.id =="))
                assert r.status_code == 422, "filter enabled put"
                r = await c.post(wid + "/disable")
                assert r.status_code == 200

                r = await c.post(wid + "/dry-run", json={"payload": {"event": "case.created", "case_id": None, "changes": {}}})
                assert r.status_code == 200, f"f {r.status_code} {r.text}"
                run_id = r.json()["run_id"]
                async with AsyncSessionLocal() as db2:
                    run = (await db2.execute(select(WorkflowRun).where(WorkflowRun.id == run_id))).scalars().first()
                    assert run.is_dry_run is True
                assert delayed == [run_id], "f delay"

                r = await c.post(f"/api/v1/workflows/{bad_id}/dry-run", json={})
                assert r.status_code == 422, "g"

                r = await c.post(wid + "/run", json={"case_id": other_case})
                assert r.status_code == 400, "h"

                r = await c.get(f"/api/v1/workflow-runs/{run_id}")
                d = r.json()
                assert r.status_code == 200 and d["children"] == [] and [s["node_id"] for s in d["steps"]] == ["start"], "k"

                r = await c.get(f"/api/v1/workflow-runs/?workflow_id={wf['id']}")
                assert [x["id"] for x in r.json()] == [run_id]
                r = await c.get("/api/v1/workflows/")
                row = next(x for x in r.json() if x["id"] == wf["id"])
                assert row["run_count"] == 1 and row["last_run_status"] is not None

                assert (await c.get(f"/api/v1/workflows/{other_wf}")).status_code == 404, "j get"
                r = await c.post(wid + "/dry-run", json={"case_id": other_case})
                assert r.status_code == 404, "j case"

                async with AsyncSessionLocal() as db2:  # cancelling a loop child must wake the parent for_each
                    loop_step = WorkflowRunStep(run_id=run_id, node_id="loop", status="waiting")
                    db2.add(loop_step)
                    await db2.flush()
                    child = WorkflowRun(tenant_id=state["tenant"], workflow_id=wf["id"], workflow_version=1, status="running",
                                        trigger_payload={}, graph_snapshot=GOOD, parent_run_id=run_id,
                                        parent_step_id=loop_step.id, loop_index=0)
                    db2.add(child)
                    await db2.commit()
                    child_id, step_id = child.id, loop_step.id
                r = await c.post(f"/api/v1/workflow-runs/{child_id}/cancel")
                assert r.status_code == 200 and r.json()["status"] == "cancelled", "l cancel"
                assert ticks == [step_id], "l tick"
                r = await c.post(f"/api/v1/workflow-runs/{run_id}/cancel")
                assert r.status_code == 200 and ticks == [step_id], "l top-level cancel must not tick"

                state["role"] = "viewer"
                r = await c.post("/api/v1/workflows/", json=_wf())
                assert r.status_code == 403, "i"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            tids = ids["tenants"]
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["workflows"])))
            await db.execute(delete(Case).where(Case.id.in_(ids["cases"])))
            await db.execute(delete(User).where(User.id.in_(ids["users"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
    await engine.dispose()


def test_workflow_api(monkeypatch):
    from app.tasks.workflows import advance_run_task
    from app.tasks.workflows import loop_tick_task
    delayed, ticks = [], []
    monkeypatch.setattr(advance_run_task, "delay", lambda rid: delayed.append(rid))
    monkeypatch.setattr(loop_tick_task, "delay", lambda sid: ticks.append(sid))
    asyncio.run(_scenario(delayed, ticks))
