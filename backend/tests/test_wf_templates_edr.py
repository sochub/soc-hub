"""EDR templates (CrowdStrike, SentinelOne): install through the API, dry-run with vendor-shaped mocks.

Runs are driven in-process (Celery .delay calls are queued and drained here), so no worker is needed.
"""
import asyncio
import secrets

import httpx
from sqlalchemy import delete, select

import app.tasks.workflows as tw
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact, ArtifactType
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.audit_log import AuditLog
from app.models.case import Alert, Case
from app.models.case_artifact import CaseArtifact
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.utils.crypto import encrypt
from app.workflows import runtime
from app.workflows import templates as tpl

SINCE = "2026-10-18T10:00:00Z"
SCHEDULE = {"event": "schedule", "fired_at": "2026-10-18T10:05:00Z", "since": SINCE,
            "case_id": None, "alert_id": None, "changes": {}}

# Shapes from the Alerts API v2 reference (developer.crowdstrike.com) and falcon-mcp field docs.
CS_IDS = {"status": 200, "headers": {}, "body": {
    "meta": {"query_time": 0.01, "pagination": {"offset": 0, "limit": 100, "total": 2}, "trace_id": "t"},
    "resources": ["d615:ind:77d11725xxxx:111", "d615:ind:77d11725xxxx:222"], "errors": []}}
CS_DETAILS = {"status": 200, "headers": {}, "body": {
    "meta": {"query_time": 0.02, "trace_id": "t"}, "errors": [],
    "resources": [
        {"composite_id": "d615:ind:77d11725xxxx:111", "aggregate_id": "aggind:77d1:1", "product": "epp",
         "severity": 90, "severity_name": "Critical", "display_name": "NtdsFileAccessedViaVss",
         "name": "NtdsFileAccessedViaVss", "created_timestamp": "2026-10-18T10:01:12.973070837Z",
         "tactic": "Credential Access", "technique": "OS Credential Dumping", "status": "new",
         "device": {"device_id": "77d11725aaaa", "hostname": "WS-01", "platform_name": "Windows"}},
        {"composite_id": "d615:ind:77d11725xxxx:222", "product": "epp", "severity": 30, "severity_name": "Low",
         "display_name": "SuspiciousScriptExecution", "created_timestamp": "2026-10-18T10:02:00Z",
         "device": {"device_id": "88e2", "hostname": "SRV-DB-2"}},
    ]}}
# Shape from the Management API v2.1 threats list (data + pagination.nextCursor).
S1_THREATS = {"status": 200, "headers": {}, "body": {
    "pagination": {"nextCursor": None, "totalItems": 2},
    "data": [
        {"id": "1890123456789012345",
         "threatInfo": {"threatName": "mimikatz.exe", "confidenceLevel": "malicious", "classification": "Malware",
                        "createdAt": "2026-10-18T10:01:00.123456Z", "sha1": "a" * 40, "mitigationStatus": "mitigated"},
         "agentRealtimeInfo": {"agentComputerName": "FIN-LT-07", "agentId": "225494730938493804"}},
        {"id": "1890123456789099999",
         "threatInfo": {"threatName": "invoice.js", "confidenceLevel": "suspicious", "classification": "Malware",
                        "createdAt": "2026-10-18T10:03:00Z"},
         "agentRealtimeInfo": {"agentComputerName": "HR-LT-02", "agentId": "225494730938400000"}},
    ]}}

PARAMS = {"FALCON_API_HOST": "api.eu-1.crowdstrike.com", "S1_CONSOLE": "usea1-acme.sentinelone.net",
          "PLAYBOOK_ID": "12345"}


def _queue(monkeypatch):
    q = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: q.append(("advance", rid)))
    monkeypatch.setattr(tw.execute_step_task, "delay", lambda sid: q.append(("exec", sid)))
    monkeypatch.setattr(tw.loop_tick_task, "delay", lambda sid: q.append(("tick", sid)))
    monkeypatch.setattr(tw.execute_step_task, "apply_async", lambda args, countdown=0: q.append(("exec", args[0])))
    return q


async def _drain(q):
    fns = {"advance": runtime.advance_run, "exec": runtime.execute_step, "tick": runtime.loop_tick}
    n = 0
    while q:
        kind, ident = q.pop(0)
        await fns[kind](ident)
        n += 1
        assert n < 500, "run did not settle"


async def _steps(run_id):
    """{node_id: step} for the run, and a list of {node_id: step} per loop child (by loop index)."""
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        top = {s.node_id: s for s in (await db.execute(
            select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))).scalars()}
        kids = (await db.execute(select(WorkflowRun).where(WorkflowRun.parent_run_id == run_id)
                                 .order_by(WorkflowRun.loop_index))).scalars().all()
        children = []
        for k in kids:
            children.append({s.node_id: s for s in (await db.execute(
                select(WorkflowRunStep).where(WorkflowRunStep.run_id == k.id))).scalars()})
        return run, top, children, [k.status for k in kids]


async def _scenario(q):
    h = secrets.token_hex(3)
    ids = {"wf": [], "alert": [], "case": [], "art": [], "ca": [], "atd": [], "sec": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-edr", slug=f"wf-edr-{h}")
        u = User(email=f"wf-edr-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.commit()
        tid, uid = t.id, u.id
        try:
            for name, host in (("FALCON_CLIENT_ID", PARAMS["FALCON_API_HOST"]),
                               ("FALCON_CLIENT_SECRET", PARAMS["FALCON_API_HOST"]),
                               ("S1_API_TOKEN", PARAMS["S1_CONSOLE"])):
                s = TenantSecret(tenant_id=tid, name=name, value_enc=encrypt(f"val-{name}-{h}"), allowed_hosts=[host])
                db.add(s)
                await db.flush()
                ids["sec"].append(s.id)
            atd = ArtifactTypeDefinition(tenant_id=tid, key="hostname", label="Hostname")
            db.add(atd)
            await db.flush()
            ids["atd"].append(atd.id)
            case = Case(title="EDR test", tenant_id=tid, tags=[])
            bare = Case(title="EDR test, no host", tenant_id=tid, tags=[])
            db.add_all([case, bare])
            await db.flush()
            ids["case"] += [case.id, bare.id]
            art = Artifact(tenant_id=tid, artifact_type=ArtifactType.OTHER, value="FIN-LT-07", custom_type_id=atd.id)
            db.add(art)
            await db.flush()
            ids["art"].append(art.id)
            ca = CaseArtifact(case_id=case.id, artifact_id=art.id)
            db.add(ca)
            cs_alert = Alert(tenant_id=tid, source="crowdstrike", external_id="d615:ind:x:1", title="t", status="pending",
                             severity="critical", payload=CS_DETAILS["body"]["resources"][0])
            s1_low = Alert(tenant_id=tid, source="sentinelone", external_id="189000", title="t", status="pending",
                           severity="low",
                           payload={"id": "189000", "threatInfo": {"confidenceLevel": "n/a"},
                                    "agentRealtimeInfo": {"agentComputerName": "HR-LT-02"}})
            db.add_all([cs_alert, s1_low])
            await db.flush()
            ids["ca"].append(ca.id)
            ids["alert"] += [cs_alert.id, s1_low.id]
            await db.commit()
            case_id, bare_id, cs_alert_id, s1_low_id = case.id, bare.id, cs_alert.id, s1_low.id

            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.require_admin] = lambda: u
            tpl.reset_cache()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                async def install(key):
                    r = await c.post(f"/api/v1/workflows/templates/{key}/install", json={"params": PARAMS})
                    assert r.status_code == 201, r.text
                    d = r.json()
                    ids["wf"].append(d["workflow"]["id"])
                    assert d["missing_secrets"] == [], d
                    return d["workflow"]["id"]

                async def dry(wf_id, **body):
                    r = await c.post(f"/api/v1/workflows/{wf_id}/dry-run", json=body)
                    assert r.status_code == 200, r.text
                    await _drain(q)
                    return await _steps(r.json()["run_id"])

                # ── CrowdStrike pull ──
                wf = await install("crowdstrike-alerts-pull")
                run, top, kids, kstat = await dry(wf, payload=SCHEDULE, mocks={"ids": CS_IDS, "details": CS_DETAILS})
                assert run.status == "succeeded", (run.error, {k: (s.status, s.error) for k, s in top.items()})
                ids_in = top["ids"].output["would_do"]
                assert ids_in["url"] == ("https://api.eu-1.crowdstrike.com/alerts/queries/alerts/v2?filter="
                                         "created_timestamp%3A%3E%3D%272026-10-18T10%3A00%3A00Z%27"
                                         "&sort=created_timestamp%7Casc&limit=100"), ids_in["url"]
                assert ids_in["auth"]["token_url"] == "https://api.eu-1.crowdstrike.com/oauth2/token"
                assert "val-FALCON" not in str(top["ids"].output)
                det = top["details"].output["would_do"]
                assert det["method"] == "POST" and det["url"] == "https://api.eu-1.crowdstrike.com/alerts/entities/alerts/v2"
                assert det["body"] == {"composite_ids": CS_IDS["body"]["resources"]}
                assert kstat == ["succeeded", "succeeded"]
                got = [k["create"].output["would_do"] for k in kids]
                assert [(g["source"], g["external_id"], g["title"], g["severity"]) for g in got] == [
                    ("crowdstrike", "d615:ind:77d11725xxxx:111", "NtdsFileAccessedViaVss on WS-01", "critical"),
                    ("crowdstrike", "d615:ind:77d11725xxxx:222", "SuspiciousScriptExecution on SRV-DB-2", "low"),
                ]
                # No ids -> the entities call and the loop are skipped.
                empty = {"status": 200, "headers": {}, "body": {"meta": {}, "resources": [], "errors": []}}
                run, top, kids, _ = await dry(wf, payload=SCHEDULE, mocks={"ids": empty})
                assert run.status == "succeeded" and top["details"].status == "skipped" and not kids
                assert top["ids_failed"].status == "skipped"
                # 401 on the id query / 429 on the entities call -> the run fails with the status, never the body.
                denied = {"status": 401, "headers": {}, "body": {"errors": [{"message": "access denied SENSITIVE"}]}}
                run, top, kids, _ = await dry(wf, payload=SCHEDULE, mocks={"ids": denied})
                assert run.status == "failed" and top["ids_failed"].status == "failed" and not kids
                assert top["ids_failed"].error == "CrowdStrike pull failed: alert id query returned HTTP 401"
                assert "SENSITIVE" not in (run.error or "") and "HTTP 401" in run.error
                throttled = {"status": 429, "headers": {}, "body": {"errors": [{"message": "SENSITIVE"}]}}
                run, top, kids, _ = await dry(wf, payload=SCHEDULE, mocks={"ids": CS_IDS, "details": throttled})
                assert run.status == "failed" and not kids
                assert top["details_failed"].error == "CrowdStrike pull failed: alert details returned HTTP 429"

                # ── SentinelOne pull ──
                wf = await install("sentinelone-threats-pull")
                run, top, kids, kstat = await dry(wf, payload=SCHEDULE, mocks={"pull": S1_THREATS})
                assert run.status == "succeeded", (run.error, {k: (s.status, s.error) for k, s in top.items()})
                pull = top["pull"].output["would_do"]
                assert pull["url"] == ("https://usea1-acme.sentinelone.net/web/api/v2.1/threats"
                                       "?createdAt__gt=2026-10-18T10%3A00%3A00Z&limit=100"), pull["url"]
                assert pull["headers"]["Authorization"].startswith("ApiToken ") and "val-S1" not in str(pull)
                assert kstat == ["succeeded", "succeeded"]
                got = [k["create"].output["would_do"] for k in kids]
                assert [(g["source"], g["external_id"], g["title"], g["severity"]) for g in got] == [
                    ("sentinelone", "1890123456789012345", "mimikatz.exe on FIN-LT-07", "high"),
                    ("sentinelone", "1890123456789099999", "invoice.js on HR-LT-02", "medium"),
                ]
                assert top["pull_failed"].status == "skipped"
                run, top, kids, _ = await dry(wf, payload=SCHEDULE, mocks={"pull": {"status": 401, "headers": {}, "body": {
                    "errors": [{"code": 4010010, "detail": "SENSITIVE"}]}}})
                assert run.status == "failed" and not kids
                assert top["pull_failed"].error == "SentinelOne pull failed: threats query returned HTTP 401"
                assert "SENSITIVE" not in (run.error or "")

                # ── triage ──
                wf = await install("edr-alerts-to-case")
                run, top, _, _ = await dry(wf, alert_id=cs_alert_id)
                assert run.status == "succeeded", (run.error, {k: (s.status, s.error) for k, s in top.items()})
                assert top["dismiss"].status == "skipped"
                p = top["promote"].output["would_do"]
                assert p["mode"] == "group" and p["group_key"] == "host:WS-01" and p["severity"] == "critical"
                assert top["host_artifact"].output["would_do"]["value"] == "WS-01"
                assert top["playbook"].output["would_do"]["template_id"] == "12345"
                run, top, _, _ = await dry(wf, alert_id=s1_low_id)
                assert run.status == "succeeded" and top["promote"].status == "skipped"
                assert top["dismiss"].output["would_do"]["reason"] == "auto: low severity sentinelone alert"

                # ── CrowdStrike contain ──
                wf = await install("crowdstrike-contain-host")
                find = {"status": 200, "headers": {}, "body": {"meta": {}, "resources": ["77d11725aaaa"], "errors": []}}
                contain = {"status": 202, "headers": {}, "body": {"meta": {}, "errors": [],
                                                                  "resources": [{"id": "77d11725aaaa", "path": ""}]}}
                run, top, _, _ = await dry(wf, case_id=case_id, mocks={"find": find, "contain": contain})
                assert run.status == "succeeded", (run.error, {k: (s.status, s.error) for k, s in top.items()})
                assert top["find"].output["would_do"]["url"] == (
                    "https://api.eu-1.crowdstrike.com/devices/queries/devices/v1?filter=hostname%3A%27FIN-LT-07%27&limit=2")
                cw = top["contain"].output["would_do"]
                assert cw["url"] == "https://api.eu-1.crowdstrike.com/devices/entities/devices-actions/v2?action_name=contain"
                assert cw["method"] == "POST" and cw["body"] == {"ids": ["77d11725aaaa"]}
                assert top["done"].output["would_do"]["content"] == (
                    "Falcon network containment requested for FIN-LT-07 (device 77d11725aaaa): device lookup HTTP 200, "
                    "contain HTTP 202 (accepted).")
                assert top["no_host"].status == "skipped" and top["not_found"].status == "skipped"
                run, top, _, _ = await dry(wf, case_id=bare_id)
                assert run.status == "succeeded" and top["find"].status == "skipped"
                assert "no hostname" in top["no_host"].output["would_do"]["content"]
                two = {"status": 200, "headers": {}, "body": {"resources": ["a", "b"]}}
                run, top, _, _ = await dry(wf, case_id=case_id, mocks={"find": two})
                assert run.status == "succeeded" and top["contain"].status == "skipped"
                assert "2 matching devices" in top["not_found"].output["would_do"]["content"]

                # ── SentinelOne disconnect ──
                wf = await install("sentinelone-disconnect-agent")
                agents = {"status": 200, "headers": {}, "body": {"pagination": {"nextCursor": None, "totalItems": 2}, "data": [
                    {"id": "225494730938493804", "computerName": "FIN-LT-07", "networkStatus": "connected"},
                    {"id": "225494730938400001", "computerName": "FIN-LT-070", "networkStatus": "connected"}]}}
                disc = {"status": 200, "headers": {}, "body": {"data": {"affected": 1}}}
                run, top, _, _ = await dry(wf, case_id=case_id, mocks={"find": agents, "disconnect": disc})
                assert run.status == "succeeded", (run.error, {k: (s.status, s.error) for k, s in top.items()})
                assert top["find"].output["would_do"]["url"] == (
                    "https://usea1-acme.sentinelone.net/web/api/v2.1/agents?computerName__like=FIN-LT-07&limit=50")
                dw = top["disconnect"].output["would_do"]
                assert dw["url"] == "https://usea1-acme.sentinelone.net/web/api/v2.1/agents/actions/disconnect"
                assert dw["method"] == "POST" and dw["body"] == {"filter": {"ids": ["225494730938493804"]}}
                assert dw["headers"]["Authorization"].startswith("ApiToken ") and "val-S1" not in str(dw)
                assert top["done"].output["would_do"]["content"] == (
                    "SentinelOne network disconnect requested for FIN-LT-07 (agent 225494730938493804): agent lookup "
                    "HTTP 200, disconnect HTTP 200, agents affected 1.")
                none = {"status": 200, "headers": {}, "body": {"data": [{"id": "1", "computerName": "FIN-LT-070"}]}}
                run, top, _, _ = await dry(wf, case_id=case_id, mocks={"find": none})
                assert run.status == "succeeded" and top["disconnect"].status == "skipped"
                assert "0 agents with that exact computer name" in top["not_found"].output["would_do"]["content"]
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))  # runs and steps cascade
            await db.execute(delete(CaseArtifact).where(CaseArtifact.id.in_(ids["ca"])))
            await db.execute(delete(Artifact).where(Artifact.id.in_(ids["art"])))
            await db.execute(delete(ArtifactTypeDefinition).where(ArtifactTypeDefinition.id.in_(ids["atd"])))
            await db.execute(delete(Alert).where(Alert.id.in_(ids["alert"])))
            await db.execute(delete(Case).where(Case.id.in_(ids["case"])))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))  # throwaway tenant's own rows
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_edr_templates_dry_run(monkeypatch):
    asyncio.run(_scenario(_queue(monkeypatch)))
