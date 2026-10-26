"""MCP tools added for REST parity (case records, artifacts, IOCs, enrichment, triage, runs) and admin authoring
(playbooks, workflows): happy paths, tenant isolation, role rules, and workflows saved disabled.

Cleanup deletes only the rows these tests created (by id, or by the test tenants' cascade)."""
import copy
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.mcp import tools
from app.mcp.tools import ToolError
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.audit_log import AuditLog
from app.models.case import TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.playbook import PlaybookTemplate
from app.models.workflow import Workflow, WorkflowRun
from tests.guest_fixtures import make_case, make_tenant
from tests.test_mcp_tools import _alert, _member, _no_side_effects, _not_found, _p, _run  # noqa: F401

GOOD = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                  {"id": "note", "type": "case_add_note", "config": {"content": "hi"}}],
        "edges": [{"id": "e1", "source": "start", "target": "note", "source_handle": None}]}
BAD = {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                 {"id": "note", "type": "case_add_note", "config": {}}], "edges": []}


async def _actions(db, tenant_id, entity_id=None):
    q = select(AuditLog.action).where(AuditLog.tenant_id == tenant_id)
    if entity_id is not None:
        q = q.where(AuditLog.entity_id == entity_id)
    return set((await db.execute(q)).scalars().all())


def test_registry_and_roles():
    admin_tools = {"create_playbook", "update_playbook", "delete_playbook", "publish_playbook", "create_workflow",
                   "update_workflow", "dry_run_workflow", "create_playbook_draft"}
    assert {n for n, r in tools.MIN_ROLE.items() if r == "admin"} == admin_tools
    assert set(tools.TOOLS) == set(tools.MIN_ROLE)
    assert not any(n in tools.MIN_ROLE for n in ("enable_workflow", "disable_workflow", "delete_workflow"))


def test_case_events_assets_tasks_milestones():
    async def body(db, ids):
        t = await make_tenant(db, ids)
        user = await _member(db, ids, t)
        case = await make_case(db, ids, t)
        p, cid, tid = _p(user, t), case.id, t.id
        art = await tools.add_artifact(db, p, case_id=cid, type_key="ip", value="10.20.30.40")
        ev = await tools.add_case_event(db, p, case_id=cid, event_date="2026-10-01T08:30:00Z", title="Logon",
                                        category="initial_access", artifact_ids=[art["id"]])
        assert ev["category"] == "initial_access" and [a["id"] for a in ev["artifacts"]] == [art["id"]]
        with pytest.raises(ToolError) as e:
            await tools.add_case_event(db, p, case_id=cid, event_date="2026-10-01T08:30:00Z", title="x",
                                       category="nonsense")
        assert "category" in str(e.value)
        up = await tools.update_case_event(db, p, case_id=cid, event_id=ev["id"], flagged=True, title="Logon 2")
        assert up["flagged"] is True and up["title"] == "Logon 2"
        listed = await tools.list_case_events(db, p, case_id=cid)
        assert listed["total"] == 1 and listed["events"][0]["id"] == ev["id"]
        await tools.delete_case_event(db, p, case_id=cid, event_id=ev["id"])
        assert (await tools.list_case_events(db, p, case_id=cid))["total"] == 0

        asset = await tools.add_case_asset(db, p, case_id=cid, name="ws-01", ip="10.1.1.1")
        assert asset["asset_type"] == "host" and asset["compromise_status"] == "unknown"
        asset = await tools.update_case_asset(db, p, case_id=cid, asset_id=asset["id"], compromise_status="compromised")
        assert asset["compromise_status"] == "compromised"
        assert [a["id"] for a in (await tools.list_case_assets(db, p, case_id=cid))["assets"]] == [asset["id"]]
        await tools.delete_case_asset(db, p, case_id=cid, asset_id=asset["id"])
        assert (await tools.list_case_assets(db, p, case_id=cid))["assets"] == []

        task = await tools.add_case_task(db, p, case_id=cid, title="Isolate host", phase="containment")
        assert task["status"] == "todo"
        with pytest.raises(ToolError):
            await tools.add_case_task(db, p, case_id=cid, title="x", phase="party")
        await tools.delete_case_task(db, p, case_id=cid, task_id=task["id"])
        assert (await tools.list_case_tasks(db, p, case_id=cid))["tasks"] == []

        r = await tools.set_case_milestones(db, p, case_id=cid, detected_at="2026-10-01T09:00:00Z")
        assert r["milestones"]["detected"]["source"] == "declared"
        with pytest.raises(ToolError) as e:  # first seen after detected
            await tools.set_case_milestones(db, p, case_id=cid, first_seen_at="2026-10-02T09:00:00Z")
        assert "must not be after" in str(e.value)
        with pytest.raises(ToolError):  # naive time
            await tools.set_case_milestones(db, p, case_id=cid, contained_at="2026-10-02T09:00:00")
        r = await tools.set_case_milestones(db, p, case_id=cid, clear=["detected"])
        assert r["milestones"]["detected"]["source"] != "declared"
        assert "milestones" in await tools.get_case(db, p, case_id=cid)

        got = await _actions(db, tid, cid)
        assert {"event_added", "event_updated", "event_deleted", "asset_added", "asset_updated", "asset_deleted",
                "task_added", "task_removed", "milestones_changed"} <= got
    _run(body)


def test_activity_entries_author_or_admin():
    async def body(db, ids):
        t = await make_tenant(db, ids)
        author, other = await _member(db, ids, t), await _member(db, ids, t)
        admin = await _member(db, ids, t, role="admin")
        case = await make_case(db, ids, t)
        note = await tools.add_timeline_note(db, _p(author, t), case_id=case.id, content="first")
        with pytest.raises(ToolError) as e:
            await tools.edit_activity_entry(db, _p(other, t), case_id=case.id, entry_id=note["id"], content="x")
        assert "author" in str(e.value)
        r = await tools.edit_activity_entry(db, _p(author, t), case_id=case.id, entry_id=note["id"], content="second")
        assert r["content"] == "second"
        r = await tools.pin_activity_entry(db, _p(other, t), case_id=case.id, entry_id=note["id"])
        assert r["pinned"] is True
        assert "timeline_pinned" in await _actions(db, t.id)
        with pytest.raises(ToolError):
            await tools.delete_activity_entry(db, _p(other, t), case_id=case.id, entry_id=note["id"])
        await tools.delete_activity_entry(db, _p(admin, t, "admin"), case_id=case.id, entry_id=note["id"])
        n = (await db.execute(select(func.count()).select_from(TimelineEvent).where(
            TimelineEvent.id == note["id"]))).scalar()
        assert n == 0
    _run(body)


def test_new_tools_tenant_isolation():
    async def body(db, ids):
        t, other = await make_tenant(db, ids), await make_tenant(db, ids)
        me = await _member(db, ids, t, role="admin")
        them = await _member(db, ids, other)
        fcase = await make_case(db, ids, other)
        case = await make_case(db, ids, t)
        fp, p = _p(them, other), _p(me, t, "admin")
        fcase, case = fcase.id, case.id
        fev = await tools.add_case_event(db, fp, case_id=fcase, event_date="2026-10-01T08:30:00Z", title="e")
        fasset = await tools.add_case_asset(db, fp, case_id=fcase, name="h")
        ftask = await tools.add_case_task(db, fp, case_id=fcase, title="t")
        fart = await tools.add_artifact(db, fp, case_id=fcase, type_key="ip", value="10.66.66.66")
        fioc = await tools.add_ioc(db, fp, ioc_type="ip_address", value="10.66.66.66")
        ids.iocs.append(fioc["id"])
        falert = await _alert(db, ids, other)
        fnote = await tools.add_timeline_note(db, fp, case_id=fcase, content="n")
        ftri = await tools.run_triage(db, fp, case_id=fcase)
        fwf = Workflow(tenant_id=other.id, name="f", trigger_type="manual", graph=GOOD, enabled=False)
        db.add(fwf)
        await db.commit()
        frun = WorkflowRun(tenant_id=other.id, workflow_id=fwf.id, workflow_version=1, status="running",
                           graph_snapshot=GOOD)
        db.add(frun)
        await db.commit()
        falert, fwf_id, frun_id = falert.id, fwf.id, frun.id
        fpb = await tools.create_playbook(db, _p(them, other, "admin"), name="F", tasks=[{"title": "t"}])

        await _not_found(tools.list_case_events(db, p, case_id=fcase))
        await _not_found(tools.update_case_event(db, p, case_id=fcase, event_id=fev["id"], title="x"))
        await _not_found(tools.update_case_event(db, p, case_id=case, event_id=fev["id"], title="x"))
        await _not_found(tools.delete_case_event(db, p, case_id=case, event_id=fev["id"]))
        await _not_found(tools.list_case_assets(db, p, case_id=fcase))
        await _not_found(tools.update_case_asset(db, p, case_id=case, asset_id=fasset["id"], name="x"))
        await _not_found(tools.delete_case_asset(db, p, case_id=fcase, asset_id=fasset["id"]))
        await _not_found(tools.delete_case_task(db, p, case_id=case, task_id=ftask["id"]))
        await _not_found(tools.set_case_milestones(db, p, case_id=fcase, detected_at="2026-10-01T09:00:00Z"))
        await _not_found(tools.edit_activity_entry(db, p, case_id=case, entry_id=fnote["id"], content="x"))
        await _not_found(tools.pin_activity_entry(db, p, case_id=fcase, entry_id=fnote["id"]))
        await _not_found(tools.list_artifacts(db, p, case_id=fcase))
        await _not_found(tools.update_artifact(db, p, artifact_id=fart["id"], description="x"))
        await _not_found(tools.remove_artifact_from_case(db, p, artifact_id=fart["id"], case_id=fcase))
        await _not_found(tools.delete_artifact(db, p, artifact_id=fart["id"]))
        await _not_found(tools.update_ioc(db, p, ioc_id=fioc["id"], status="inactive"))
        await _not_found(tools.delete_ioc(db, p, ioc_id=fioc["id"]))
        await _not_found(tools.similar_alerts(db, p, alert_id=falert))
        await _not_found(tools.run_enrichment(db, p, kind="ioc", id=fioc["id"], confirm=True))
        await _not_found(tools.run_triage(db, p, case_id=fcase))
        await _not_found(tools.get_triage(db, p, case_id=fcase))
        await _not_found(tools.resolve_triage_item(db, p, case_id=case, triage_id=ftri["triage_id"],
                                                   field="playbook", status="dismissed"))
        await _not_found(tools.cancel_workflow_run(db, p, run_id=frun_id))
        await _not_found(tools.get_workflow(db, p, workflow_id=fwf_id))
        await _not_found(tools.validate_workflow(db, p, workflow_id=fwf_id))
        await _not_found(tools.update_workflow(db, p, workflow_id=fwf_id, name="x"))
        await _not_found(tools.dry_run_workflow(db, p, workflow_id=fwf_id))
        await _not_found(tools.update_playbook(db, p, playbook_id=fpb["id"], name="x"))
        await _not_found(tools.publish_playbook(db, p, playbook_id=fpb["id"]))
        await _not_found(tools.delete_playbook(db, p, playbook_id=fpb["id"]))
        assert [r["id"] for r in (await tools.list_workflow_runs(db, p))["runs"]] == []
        assert (await tools.list_workflows(db, p))["workflows"] == []
        assert (await tools.list_iocs(db, p))["iocs"] == []
        assert fart["id"] not in [a["id"] for a in (await tools.list_artifacts(db, p, value="10.66.66.66"))["artifacts"]]
        # nothing foreign changed
        fwf = (await db.execute(select(Workflow).where(Workflow.id == fwf_id))).scalars().one()
        frun = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == frun_id))).scalars().one()
        assert fwf.name == "f" and fwf.version == 1 and frun.status == "running"
        pb = (await db.execute(select(PlaybookTemplate).where(PlaybookTemplate.id == fpb["id"]))).scalars().one()
        assert pb.status == "draft" and pb.name == "F"
    _run(body)


def test_artifacts_iocs_alerts():
    async def body(db, ids):
        t = await make_tenant(db, ids)
        user = await _member(db, ids, t)
        c1, c2 = await make_case(db, ids, t), await make_case(db, ids, t)
        p, tid = _p(user, t), t.id
        c1, c2 = c1.id, c2.id
        a = await _alert(db, ids, t, payload={"src_ip": "203.0.113.7"})
        b = await _alert(db, ids, t, payload={"dst": "203.0.113.7"})
        await _alert(db, ids, t, payload={"src_ip": "198.51.100.1"})
        assert [x["id"] for x in (await tools.similar_alerts(db, p, alert_id=a.id))["alerts"]] == [b.id]

        shared = await tools.add_artifact(db, p, case_id=c1, type_key="domain", value="evil.example")
        await tools.add_artifact(db, p, case_id=c2, type_key="domain", value="evil.example")
        listed = (await tools.list_artifacts(db, p, value="evil.example"))["artifacts"]
        assert len(listed) == 1 and listed[0]["case_ids"] == sorted([c1, c2])
        assert [a["id"] for a in (await tools.list_artifacts(db, p, case_id=c1))["artifacts"]] == [shared["id"]]
        r = await tools.update_artifact(db, p, artifact_id=shared["id"], description="C2 domain", type_key="url")
        assert r["type"] == "url" and r["description"] == "C2 domain"
        db.add(ArtifactTypeDefinition(tenant_id=tid, key="vault_ref", label="V", private=True))
        await db.commit()
        with pytest.raises(ToolError):
            await tools.update_artifact(db, p, artifact_id=shared["id"], type_key="vault_ref")
        await tools.remove_artifact_from_case(db, p, artifact_id=shared["id"], case_id=c1)
        r = (await tools.list_artifacts(db, p, value="evil.example"))["artifacts"]
        assert r[0]["case_ids"] == [c2]
        await _not_found(tools.remove_artifact_from_case(db, p, artifact_id=shared["id"], case_id=c1))
        iso = await tools.add_artifact(db, p, case_id=c1, type_key="ip", value="10.5.5.5", isolated=True)
        await tools.remove_artifact_from_case(db, p, artifact_id=iso["id"], case_id=c1)
        assert (await tools.list_artifacts(db, p, value="10.5.5.5"))["artifacts"] == []  # orphaned isolated: gone
        await tools.delete_artifact(db, p, artifact_id=shared["id"])
        assert (await tools.list_artifacts(db, p, value="evil.example"))["artifacts"] == []
        removed = (await db.execute(select(TimelineEvent.case_id).where(
            TimelineEvent.event_type == "artifact_removed", TimelineEvent.case_id.in_([c1, c2])))).scalars().all()
        assert sorted(removed) == sorted([c1, c1, c2])

        ioc = await tools.add_ioc(db, p, ioc_type="domain", value="bad.example", case_id=c1)
        ids.iocs.append(ioc["id"])
        assert [i["id"] for i in (await tools.list_iocs(db, p, case_id=c1))["iocs"]] == [ioc["id"]]
        r = await tools.update_ioc(db, p, ioc_id=ioc["id"], threat_level="high", tags=["c2"])
        assert r["threat_level"] == "high" and set(r["changed"]) == {"threat_level", "tags"}
        r = await tools.update_ioc(db, p, ioc_id=ioc["id"], unlink_case=True)
        assert r["case_id"] is None
        await tools.delete_ioc(db, p, ioc_id=ioc["id"])
        assert (await tools.list_iocs(db, p))["iocs"] == []
        assert {"update", "delete"} <= await _actions(db, tid, ioc["id"])

    _run(body)


def test_enrichment_triage_runs(monkeypatch, _no_side_effects):
    async def body(db, ids):
        t = await make_tenant(db, ids)
        user = await _member(db, ids, t)
        case = await make_case(db, ids, t)
        p, tid, cid, uid = _p(user, t), t.id, case.id, user.id
        ioc = await tools.add_ioc(db, p, ioc_type="ip_address", value="203.0.113.9")  # amber by default
        ids.iocs.append(ioc["id"])
        monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", True)
        with pytest.raises(ToolError) as e:
            await tools.run_enrichment(db, p, kind="ioc", id=ioc["id"])
        assert "confirm=true" in str(e.value)
        r = await tools.run_enrichment(db, p, kind="ioc", id=ioc["id"], confirm=True)
        assert r["kind"] == "ioc" and isinstance(r["pending"], list)
        monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", False)
        with pytest.raises(ToolError) as e:
            await tools.run_enrichment(db, p, kind="ioc", id=ioc["id"], confirm=True)
        assert "disabled" in str(e.value)

        with pytest.raises(ToolError):
            await tools.get_triage(db, p, case_id=cid)
        tri = await tools.run_triage(db, p, case_id=cid)
        assert tri["triage_id"] in _no_side_effects["triage"]
        got = await tools.get_triage(db, p, case_id=cid)
        assert got["id"] == tri["triage_id"] and got["status"] == "pending"
        r = await tools.resolve_triage_item(db, p, case_id=cid, triage_id=tri["triage_id"], field="next_steps",
                                            status="dismissed")
        assert r["next_steps_status"] == "dismissed"
        row = (await db.execute(select(CaseTriageResult).where(CaseTriageResult.id == tri["triage_id"]))).scalars().one()
        assert row.triggered_by_user_id == uid

        wf = Workflow(tenant_id=tid, name="w", trigger_type="manual", graph=GOOD, enabled=True)
        db.add(wf)
        await db.commit()
        run = (await tools.run_workflow(db, p, workflow_id=wf.id, case_id=cid))["run_id"]
        runs = (await tools.list_workflow_runs(db, p, case_id=cid))["runs"]
        assert [x["id"] for x in runs] == [run] and runs[0]["workflow_name"] == "w"
        r = await tools.cancel_workflow_run(db, p, run_id=run)
        assert r["status"] == "cancelled"
        assert "cancel" in await _actions(db, tid, run)
    _run(body)


def test_playbook_authoring():
    async def body(db, ids):
        t = await make_tenant(db, ids)
        admin = await _member(db, ids, t, role="admin")
        analyst = await _member(db, ids, t)
        pa, pn, tid = _p(admin, t, "admin"), _p(analyst, t), t.id
        with pytest.raises(ToolError):
            await tools.create_playbook(db, pn, name="X")
        pb = await tools.create_playbook(db, pa, name="Phishing", category="phishing",
                                         tasks=[{"phase": "identification", "title": "Pull headers"}])
        assert pb["status"] == "draft" and [x["title"] for x in pb["tasks"]] == ["Pull headers"]
        with pytest.raises(ToolError):
            await tools.create_playbook(db, pa, name="Y", tasks=[{"phase": "bogus", "title": "t"}])
        for fn, kw in ((tools.update_playbook, {"name": "x"}), (tools.publish_playbook, {}),
                       (tools.delete_playbook, {})):
            with pytest.raises(ToolError) as e:
                await fn(db, pn, playbook_id=pb["id"], **kw)
            assert "admin" in str(e.value)
        r = await tools.update_playbook(db, pa, playbook_id=pb["id"], tasks=[
            {"phase": "containment", "title": "Block sender"}, {"phase": "eradication", "title": "Purge mail"}])
        assert [x["title"] for x in r["tasks"]] == ["Block sender", "Purge mail"] and r["status"] == "draft"
        await _not_found(tools.get_playbook(db, pn, playbook_id=pb["id"]))  # draft hidden from analysts
        r = await tools.publish_playbook(db, pa, playbook_id=pb["id"])
        assert r["status"] == "published"
        assert (await tools.get_playbook(db, pn, playbook_id=pb["id"]))["name"] == "Phishing"
        assert "playbook_published" in await _actions(db, tid, pb["id"])
        direct = await tools.create_playbook(db, pa, name="Now", publish=True)
        assert direct["status"] == "published"
        await tools.delete_playbook(db, pa, playbook_id=pb["id"])
        await _not_found(tools.get_playbook(db, pa, playbook_id=pb["id"]))
    _run(body)


def test_workflow_authoring_saved_disabled(_no_side_effects):
    async def body(db, ids):
        t = await make_tenant(db, ids)
        admin = await _member(db, ids, t, role="admin")
        analyst = await _member(db, ids, t)
        pa, pn, tid, aid = _p(admin, t, "admin"), _p(analyst, t), t.id, admin.id
        case = await make_case(db, ids, t)
        cid = case.id
        for fn, kw in ((tools.create_workflow, {"name": "x", "trigger_type": "manual", "graph": GOOD}),
                       (tools.update_workflow, {"workflow_id": 1, "name": "x"}),
                       (tools.dry_run_workflow, {"workflow_id": 1})):
            with pytest.raises(ToolError) as e:
                await fn(db, pn, **kw)
            assert "admin" in str(e.value)

        bad = await tools.create_workflow(db, pa, name="Draft", trigger_type="case.created", graph=BAD)
        assert bad["enabled"] is False and bad["version"] == 1 and bad["validation_errors"]
        wf = await tools.create_workflow(db, pa, name="Good", trigger_type="manual", graph=GOOD)
        assert wf["enabled"] is False and wf["validation_errors"] == [] and "disabled" in wf["note"]
        r = await tools.update_workflow(db, pa, workflow_id=bad["id"], graph=GOOD, description="fixed")
        assert r["enabled"] is False and r["version"] == 2 and r["validation_errors"] == []
        assert r["trigger_type"] == "case.created" and r["description"] == "fixed"
        row = (await db.execute(select(Workflow).where(Workflow.id == bad["id"]))).scalars().one()
        assert row.enabled is False and row.created_by == aid
        assert {"create", "update"} <= await _actions(db, tid, bad["id"])

        # an enabled workflow cannot be edited over MCP, and stays as it was
        row.enabled = True
        await db.commit()
        with pytest.raises(ToolError) as e:
            await tools.update_workflow(db, pa, workflow_id=row.id, graph=BAD)
        assert "enabled" in str(e.value)
        await db.refresh(row)
        assert row.enabled is True and row.version == 2 and row.graph == GOOD

        v = await tools.validate_workflow(db, pn, graph=BAD, trigger_type="manual")
        assert v["valid"] is False and v["errors"]
        assert (await tools.validate_workflow(db, pn, workflow_id=wf["id"]))["valid"] is True
        assert (await tools.validate_workflow(db, pn, workflow_id=wf["id"], graph=BAD))["valid"] is False
        with pytest.raises(ToolError):
            await tools.validate_workflow(db, pn, graph=GOOD)
        assert {w["id"] for w in (await tools.list_workflows(db, pn))["workflows"]} == {bad["id"], wf["id"]}
        assert (await tools.get_workflow(db, pn, workflow_id=wf["id"]))["graph"] == GOOD

        r = await tools.dry_run_workflow(db, pa, workflow_id=wf["id"], case_id=cid, mocks={"note": {"ok": 1}})
        run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == r["run_id"]))).scalars().one()
        assert run.is_dry_run and run.case_id == cid and r["run_id"] in _no_side_effects["runs"]
        live = copy.deepcopy(GOOD)
        live["nodes"].append({"id": "call", "type": "http_request", "execute_in_dry_run": True,
                              "config": {"method": "GET", "url": "https://example.com"}})
        live["edges"].append({"id": "e2", "source": "note", "target": "call", "source_handle": None})
        hot = await tools.create_workflow(db, pa, name="Hot", trigger_type="manual", graph=live)
        with pytest.raises(ToolError) as e:
            await tools.dry_run_workflow(db, pa, workflow_id=hot["id"])
        assert "execute during dry runs" in str(e.value)
        n = (await db.execute(select(func.count()).select_from(WorkflowRun).where(
            WorkflowRun.workflow_id == hot["id"]))).scalar()
        assert n == 0
        await _not_found(tools.dry_run_workflow(db, pa, workflow_id=wf["id"], case_id=999999999))
    _run(body)
