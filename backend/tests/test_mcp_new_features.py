"""MCP tools for suppression updates, containment ledger/release, task workflow links + results, ticket sync and
the client summary: happy paths, tenant isolation and the human-only rules (no enabling, no policy edits)."""
import pytest
from sqlalchemy import delete

from app.mcp import tools
from app.mcp.tools import ToolError
from app.models.case_task import CaseTask
from app.models.containment import ContainmentAction
from app.models.workflow import Workflow
from tests.guest_fixtures import make_case, make_tenant
from tests.test_mcp_tools import _member, _p, _run

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}}], "edges": []}


def test_new_feature_tools():
    async def body(db, ids):
        t, t2 = await make_tenant(db, ids), await make_tenant(db, ids)
        user, other = await _member(db, ids, t), await _member(db, ids, t2)
        p, p2 = _p(user, t), _p(other, t2)
        cid = (await make_case(db, ids, t)).id
        wf = Workflow(tenant_id=t.id, name="Contain", trigger_type="manual", enabled=True, graph=GRAPH)
        db.add(wf)
        await db.flush()
        act = ContainmentAction(tenant_id=t.id, case_id=cid, action="Contain host", target_kind="host",
                                target="ws-01", status="active", release_hint="lift it")
        db.add(act)
        await db.commit()
        wf_id, act_id = wf.id, act.id

        # tasks: link on create, results listed, unlink
        task = await tools.add_case_task(db, p, case_id=cid, title="Isolate", phase="containment", workflow_id=wf_id)
        assert task["workflow_id"] == wf_id and task["workflow_runnable"] is True
        with pytest.raises(ToolError):
            await tools.add_case_task(db, p2, case_id=cid, title="x")
        row = await db.get(CaseTask, task["id"])
        row.result, row.result_status = "Falcon accepted (HTTP 202)", "ok"
        await db.commit()
        listed = (await tools.list_case_tasks(db, p, case_id=cid))["tasks"][0]
        assert listed["result"] == "Falcon accepted (HTTP 202)" and listed["result_status"] == "ok"
        assert "workflow_id" not in await tools.update_task(db, p, case_id=cid, task_id=task["id"], unlink_workflow=True)

        # suppression: draft, change, disable; enabling refused
        s = await tools.create_suppression(db, p, name="noise", conditions=[{"field": "source", "op": "equals", "value": "x"}])
        u = await tools.update_suppression(db, p, suppression_id=s["id"], name="scanner noise", expires_in_days=7)
        assert u["name"] == "scanner noise" and u["enabled"] is False
        with pytest.raises(ToolError, match="person"):
            await tools.update_suppression(db, p, suppression_id=s["id"], enabled=True)
        with pytest.raises(ToolError):
            await tools.update_suppression(db, p2, suppression_id=s["id"], name="x")
        await db.refresh(user)

        # containment: policy read-only, ledger, release once, isolation
        assert (await tools.get_containment_policy(db, p))["daily_cap"] > 0
        assert not hasattr(tools, "update_containment_policy")
        acts = (await tools.list_containment_actions(db, p, case_id=cid))["actions"]
        assert [a["id"] for a in acts] == [act_id] and acts[0]["release_hint"] == "lift it"
        assert (await tools.list_containment_actions(db, p2))["actions"] == []
        with pytest.raises(ToolError):
            await tools.release_containment_action(db, p2, action_id=act_id)
        await db.refresh(user)  # one shared session here: the other tenant's failed calls rolled back (expired) it
        assert (await tools.release_containment_action(db, p, action_id=act_id))["status"] == "released"
        with pytest.raises(ToolError, match="released"):
            await tools.release_containment_action(db, p, action_id=act_id)

        # ticket sync needs a synced ticket; summary works for the token's own tenant
        with pytest.raises(ToolError, match="not set up"):
            await tools.sync_task_ticket(db, p, case_id=cid, task_id=task["id"])
        summary = await tools.get_client_summary(db, p, days=30)
        assert summary["cases_opened"] >= 1 and "security summary" in summary["text"]
        with pytest.raises(ToolError):
            await tools.get_client_summary(db, p, days=0)

        for name in ("get_containment_policy", "list_containment_actions", "get_client_summary"):
            assert tools.MIN_ROLE[name] == "viewer"
        for name in ("release_containment_action", "update_suppression", "sync_task_ticket", "create_task_ticket"):
            assert tools.MIN_ROLE[name] == "analyst"
        await db.execute(delete(CaseTask).where(CaseTask.case_id == cid))
        await db.execute(delete(ContainmentAction).where(ContainmentAction.id == act_id))
        await db.execute(delete(Workflow).where(Workflow.id == wf_id))
        await db.commit()
    _run(body)
