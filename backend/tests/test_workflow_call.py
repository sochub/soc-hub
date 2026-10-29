"""run_workflow: one workflow starts another on the same case (by name or id) with inputs, through the real queue;
refusals (missing, disabled, other tenant, itself, chain too deep, no case) and the dry-run stub; and the packaged
Escalate to incident template run by name."""
import asyncio
import json
import secrets
from pathlib import Path

from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseSeverity, CaseStatus, TimelineEvent
from app.models.tenant import Tenant
from app.models.workflow import Workflow, WorkflowRun
from app.workflows.runtime import create_run
from app.workflows.validation import validate_graph
from tests.test_wf_templates_edr import _drain, _queue

ESCALATE = json.loads((Path(__file__).resolve().parents[1] / "app" / "workflows" / "templates"
                       / "case-escalate-incident.json").read_text())


def _caller(**cfg):
    return {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                      {"id": "call", "type": "run_workflow", "config": cfg}],
            "edges": [{"id": "e", "source": "start", "target": "call", "source_handle": None}]}


async def _scenario(q):
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t, other = Tenant(name="wc", slug=f"wc-{h}"), Tenant(name="wc-o", slug=f"wc-o-{h}")
        db.add_all([t, other])
        await db.flush()
        g = ESCALATE["graph"]
        no_slack = {"nodes": [n for n in g["nodes"] if n["id"] != "notify"],
                    "edges": [e for e in g["edges"] if e["target"] != "notify"]}
        esc = Workflow(tenant_id=t.id, name="Escalate to incident", trigger_type="manual", enabled=True, graph=no_slack)
        off = Workflow(tenant_id=t.id, name="Off", trigger_type="manual", enabled=False, graph=no_slack)
        theirs = Workflow(tenant_id=other.id, name="Theirs", trigger_type="manual", enabled=True, graph=no_slack)
        case = Case(title="Login from Lagos", tenant_id=t.id, severity=CaseSeverity.MEDIUM, tags=[])
        db.add_all([esc, off, theirs, case])
        await db.commit()
        tid, oid, cid, esc_id, off_id, theirs_id = t.id, other.id, case.id, esc.id, off.id, theirs.id

    async def run(graph, *, depth=0, case_id=cid, dry=False):
        async with AsyncSessionLocal() as db:
            wf = Workflow(tenant_id=tid, name=f"caller-{secrets.token_hex(2)}", trigger_type="manual", enabled=True, graph=graph)
            db.add(wf)
            await db.flush()
            r = await create_run(db, wf, trigger_payload={"event": "manual", "case_id": case_id}, case_id=case_id,
                                 depth=depth, is_dry_run=dry)
            await db.commit()
            rid, wid = r.id, wf.id
        from app.workflows.runtime import advance_run
        await advance_run(rid)
        await _drain(q)
        async with AsyncSessionLocal() as db:
            parent = await db.get(WorkflowRun, rid)
            kids = (await db.execute(select(WorkflowRun).where(WorkflowRun.workflow_id.in_([esc_id, off_id, theirs_id]),
                                                               WorkflowRun.id > rid))).scalars().all()
            return parent, kids, wid

    try:
        reason = "{{ 'alice did not confirm' }}"
        parent, kids, wid = await run(_caller(workflow_name="Escalate to incident", inputs={"reason": reason}))
        assert parent.status == "succeeded", parent.error
        assert len(kids) == 1 and kids[0].status == "succeeded" and kids[0].case_id == cid and kids[0].depth == 1
        tp = kids[0].trigger_payload
        assert tp["inputs"] == {"reason": "alice did not confirm"} and tp["started_by"]["workflow_id"] == wid
        async with AsyncSessionLocal() as db:
            c = await db.get(Case, cid)
            assert c.severity == CaseSeverity.CRITICAL and c.status == CaseStatus.IN_PROGRESS
            assert {"incident", "escalated"} <= set(c.tags)
            notes = list((await db.execute(select(TimelineEvent.content).where(TimelineEvent.case_id == cid))).scalars())
            assert "[automation] **Escalated to an incident**: alice did not confirm" in notes, notes

        parent, kids, _ = await run(_caller(workflow_id=str(esc_id)))  # by id; no inputs -> by hand wording
        assert parent.status == "succeeded" and len(kids) == 1

        for cfg, msg in (({"workflow_name": "Nope"}, "no manual workflow 'Nope'"),
                         ({"workflow_name": "Off"}, "is disabled"),
                         ({"workflow_id": theirs_id}, "no manual workflow"),  # another tenant's
                         ({"workflow_id": "x"}, "must be an integer")):
            parent, kids, _ = await run(_caller(**cfg))
            assert parent.status == "failed" and msg in (parent.error or ""), (cfg, parent.error)
            assert kids == []
        parent, kids, _ = await run(_caller(workflow_name="Escalate to incident"), depth=3)
        assert parent.status == "failed" and "too many workflows" in parent.error and kids == []
        parent, kids, _ = await run(_caller(workflow_name="Escalate to incident"), case_id=None)
        assert parent.status == "failed" and "no case" in parent.error and kids == []
        parent, kids, _ = await run(_caller(workflow_name="Escalate to incident"), dry=True)  # simulated: starts nothing
        assert parent.status == "succeeded" and kids == []

        async with AsyncSessionLocal() as db:  # a workflow can't call itself
            me = Workflow(tenant_id=tid, name="Loop", trigger_type="manual", enabled=True, graph=_caller(workflow_name="Loop"))
            db.add(me)
            await db.flush()
            r = await create_run(db, me, trigger_payload={"event": "manual", "case_id": cid}, case_id=cid)
            await db.commit()
            rid = r.id
        from app.workflows.runtime import advance_run
        await advance_run(rid)
        await _drain(q)
        async with AsyncSessionLocal() as db:
            r = await db.get(WorkflowRun, rid)
            assert r.status == "failed" and "can't start itself" in r.error

        errs = validate_graph(_caller(), "manual")
        assert any("set the workflow to run" in e["message"] for e in errs), errs
        assert any("inputs must be a JSON object" in e["message"]
                   for e in validate_graph(_caller(workflow_name="x", inputs="nope"), "manual"))
        assert validate_graph(ESCALATE["graph"], "manual") == []
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Workflow).where(Workflow.tenant_id.in_([tid, oid])))  # runs and steps cascade
            await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == cid))
            await db.execute(delete(Case).where(Case.id == cid))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([tid, oid])))
            await db.execute(delete(Tenant).where(Tenant.id.in_([tid, oid])))
            await db.commit()
        await engine.dispose()


def test_run_workflow_node(monkeypatch):
    asyncio.run(_scenario(_queue(monkeypatch)))
