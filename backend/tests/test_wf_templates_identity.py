"""Install the identity / Microsoft templates through the API and dry-run them with vendor-shaped mock responses.

Mock bodies follow the vendors' documented response shapes:
- Okta System Log LogEvent: https://developer.okta.com/docs/api/openapi/okta-management/management/tag/SystemLog/
- Graph security alert (alerts_v2): https://learn.microsoft.com/en-us/graph/api/security-list-alerts_v2?view=graph-rest-1.0
- Graph riskDetection: https://learn.microsoft.com/en-us/graph/api/riskdetection-list?view=graph-rest-1.0
- Okta user: https://developer.okta.com/docs/api/openapi/okta-management/management/tag/User/
"""
import asyncio
import secrets

import httpx
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact, ArtifactType
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

OKTA = "acme.okta.com"
AZ_TENANT = "b3c1b5fc-828c-45fa-a1e1-10d74f6d6e9c"
GRAPH_HOSTS = ["login.microsoftonline.com", "graph.microsoft.com"]

OKTA_EVENTS = [
    {"uuid": "dc9fd3c0-598c-11ef-8478-2b7584bf8d5a", "published": "2026-10-18T10:01:02.123Z",
     "eventType": "security.threat.detected", "version": "0", "severity": "WARN",
     "displayMessage": "Request from suspicious actor",
     "actor": {"id": "00u1abcd", "type": "User", "alternateId": "olack@adatum.com", "displayName": "Olivia Lack"},
     "client": {"ipAddress": "203.0.113.7"}, "outcome": {"result": "DENY", "reason": "Password Spray"},
     "target": [], "transaction": {"type": "WEB", "id": "Zs1"}},
    {"uuid": "e1a2b3c4-598c-11ef-8478-2b7584bf8d5b", "published": "2026-10-18T10:02:03.456Z",
     "eventType": "user.account.privilege.grant", "version": "0", "severity": "INFO",
     "displayMessage": "Grant user privilege",
     "actor": {"id": "00u9admin", "type": "User", "alternateId": "admin@adatum.com", "displayName": "Admin"},
     "client": {"ipAddress": "198.51.100.4"}, "outcome": {"result": "SUCCESS"},
     "target": [{"id": "00u1abcd", "type": "User", "alternateId": "olack@adatum.com"}]},
]

M365_ALERTS = {"value": [
    {"@odata.type": "#microsoft.graph.security.alert", "id": "da637551227677560813_-961444813",
     "providerAlertId": "da637551227677560813_-961444813", "incidentId": "28282", "status": "new",
     "severity": "low", "serviceSource": "microsoftDefenderForEndpoint", "detectionSource": "antivirus",
     "title": "Suspicious execution of hidden file", "createdDateTime": "2026-10-18T10:00:27.7211305Z",
     "evidence": [{"@odata.type": "#microsoft.graph.security.deviceEvidence", "hostName": "yonif-lap3"}]},
    {"@odata.type": "#microsoft.graph.security.alert", "id": "da637878227677560813_-334257894",
     "providerAlertId": "da637878227677560813_-334257894", "incidentId": "33", "status": "new",
     "severity": "informational", "serviceSource": "microsoftDefenderForIdentity",
     "title": "Suspicious sign-in activity detected", "createdDateTime": "2026-10-18T10:03:00.0000000Z",
     "evidence": [{"@odata.type": "#microsoft.graph.security.userEvidence",
                   "userAccount": {"accountName": "olack", "userPrincipalName": "olack@adatum.com"}}]},
]}

ENTRA_RISKS = {"value": [
    {"id": "6a5874ca-abcd-9d82-5ad39bd71600", "requestId": "6a5874ca-abcd-9d82-5ad39bd71600",
     "riskEventType": "unfamiliarFeatures", "riskState": "atRisk", "riskLevel": "medium",
     "riskDetail": "none", "source": "activeDirectory", "detectionTimingType": "realtime", "activity": "signin",
     "ipAddress": "203.0.113.9", "detectedDateTime": "2026-10-18T10:11:27.773602Z",
     "userId": "abcdefab-af90-4edf-ac4c-742ff06735d0", "userDisplayName": "Olivia Lack",
     "userPrincipalName": "olack@adatum.com"},
    {"id": "7b6985db-bcde-0e93-6be40ce82711", "requestId": "7b6985db-bcde-0e93-6be40ce82711",
     "riskEventType": "generic", "riskState": "atRisk", "riskLevel": "hidden",
     "riskDetail": "hidden", "source": "IdentityProtection", "detectionTimingType": "offline", "activity": "user",
     "detectedDateTime": "2026-10-18T10:12:00Z", "userId": "bcdefabc-af90-4edf-ac4c-742ff06735d1",
     "userDisplayName": "Ray Ng", "userPrincipalName": "rng@adatum.com"},
]}


async def _drain(queue) -> None:
    while queue:
        kind, arg = queue.pop(0)
        await {"advance": runtime.advance_run, "step": runtime.execute_step, "tick": runtime.loop_tick}[kind](arg)


async def _steps(run_id: int, expect: str = "succeeded") -> dict:
    """{node_id: [(status, output), ...]} for the run and its loop children; asserts the top-level run status."""
    async with AsyncSessionLocal() as db:
        runs = (await db.execute(select(WorkflowRun).where(
            (WorkflowRun.id == run_id) | (WorkflowRun.parent_run_id == run_id)))).scalars().all()
        top = next(r for r in runs if r.id == run_id)
        assert top.status == expect, (top.status, top.error)
        assert all(r.status == "succeeded" for r in runs if r.id != run_id), [(r.id, r.status, r.error) for r in runs]
        rows = (await db.execute(select(WorkflowRunStep).where(
            WorkflowRunStep.run_id.in_([r.id for r in runs])).order_by(WorkflowRunStep.id))).scalars().all()
    out: dict = {}
    for s in rows:
        out.setdefault(s.node_id, []).append((s.status, s.output))
    return out


async def _scenario(queue):
    h = secrets.token_hex(3)
    ids = {"wf": [], "case": None, "art": None, "alert": None, "sec": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-tpl-id", slug=f"wf-tpl-id-{h}")
        u = User(email=f"wf-tpl-id-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.flush()
        tid, uid = t.id, u.id
        try:
            for name, hosts in (("OKTA_API_TOKEN", [OKTA]), ("OKTA_RESPONSE_API_TOKEN", [OKTA]),
                                ("MS_CLIENT_ID", GRAPH_HOSTS), ("MS_CLIENT_SECRET", GRAPH_HOSTS),
                                ("ENTRA_RESPONSE_CLIENT_ID", GRAPH_HOSTS), ("ENTRA_RESPONSE_CLIENT_SECRET", GRAPH_HOSTS)):
                s = TenantSecret(tenant_id=tid, name=name, value_enc=encrypt("x-" + secrets.token_hex(4)), allowed_hosts=hosts)
                db.add(s)
                await db.flush()
                ids["sec"].append(s.id)
            case = Case(title="Compromised account", tenant_id=tid, tags=["entra-disable"])
            db.add(case)
            await db.flush()
            ids["case"] = case.id
            art = Artifact(tenant_id=tid, artifact_type=ArtifactType.EMAIL, value="olack@adatum.com")
            db.add(art)
            await db.flush()
            ids["art"] = art.id
            db.add(CaseArtifact(case_id=case.id, artifact_id=art.id))
            alert = Alert(tenant_id=tid, source="m365-defender", external_id=M365_ALERTS["value"][1]["id"],
                          title="Suspicious sign-in activity detected", payload=M365_ALERTS["value"][1], status="pending",
                          severity="high")
            db.add(alert)
            await db.flush()
            ids["alert"] = alert.id
            await db.commit()

            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.require_admin] = lambda: u
            tpl.reset_cache()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                async def install_and_run(key, params, body, expect="succeeded"):
                    r = await c.post(f"/api/v1/workflows/templates/{key}/install", json={"params": params})
                    assert r.status_code == 201, r.text
                    d = r.json()
                    ids["wf"].append(d["workflow"]["id"])
                    assert d["workflow"]["validation_errors"] in (None, []), d["workflow"]
                    assert d["missing_secrets"] == [], key
                    r = await c.post(f"/api/v1/workflows/{d['workflow']['id']}/dry-run", json=body)
                    assert r.status_code == 200, r.text
                    await _drain(queue)
                    return await _steps(r.json()["run_id"], expect), d

                since = {"event": "schedule", "fired_at": "2026-10-18T10:05:00Z", "since": "2026-10-18T10:00:00Z",
                         "case_id": None, "alert_id": None, "changes": {}}

                # Okta System Log
                steps, d = await install_and_run("okta-system-log-pull", {"OKTA_DOMAIN": OKTA},
                                                 {"payload": since, "mocks": {"pull": {"status": 200, "headers": {}, "body": OKTA_EVENTS}}})
                assert d["secret_hosts"] == {"OKTA_API_TOKEN": [OKTA]}
                url = steps["pull"][0][1]["would_do"]["url"]
                assert url.startswith(f"https://{OKTA}/api/v1/logs?since=2026-10-18T10%3A00%3A00Z&sortOrder=ASCENDING&limit=200&filter=")
                assert "eventType%20eq%20%22security.threat.detected%22%20or%20" in url and " " not in url and '"' not in url
                assert "⟦secret:OKTA_API_TOKEN#" in steps["pull"][0][1]["would_do"]["headers"]["Authorization"]
                got = sorted((o["would_do"]["source"], o["would_do"]["external_id"], o["would_do"]["title"], o["would_do"]["severity"])
                             for _, o in steps["create"])
                assert got == [
                    ("okta", "dc9fd3c0-598c-11ef-8478-2b7584bf8d5a", "Request from suspicious actor (olack@adatum.com)", "medium"),
                    ("okta", "e1a2b3c4-598c-11ef-8478-2b7584bf8d5b", "Grant user privilege (admin@adatum.com)", "low"),
                ], got

                # Okta: a failed pull writes nothing and fails the run with the status only (never the body)
                steps, _ = await install_and_run("okta-system-log-pull", {"OKTA_DOMAIN": OKTA},
                                                 {"payload": since, "mocks": {"pull": {"status": 401, "headers": {},
                                                                                       "body": {"errorCode": "E0000011", "errorSummary": "Invalid token provided"}}}},
                                                 expect="failed")
                assert "create" not in steps and steps["each"][0][0] in ("skipped", "cancelled"), steps
                async with AsyncSessionLocal() as db2:
                    fstep = (await db2.execute(select(WorkflowRunStep).where(
                        WorkflowRunStep.run_id == (await db2.execute(select(WorkflowRun.id).where(
                            WorkflowRun.workflow_id == ids["wf"][-1]))).scalar_one(),
                        WorkflowRunStep.node_id == "failed"))).scalars().one()
                assert fstep.status == "failed" and fstep.error == "Okta pull failed: HTTP 401", (fstep.status, fstep.error)

                # M365 Defender alerts_v2
                steps, d = await install_and_run("m365-defender-alerts-pull", {"AZURE_TENANT_ID": AZ_TENANT},
                                                 {"payload": since, "mocks": {"pull": {"status": 200, "headers": {}, "body": M365_ALERTS}}})
                assert d["secret_hosts"] == {"MS_CLIENT_ID": GRAPH_HOSTS, "MS_CLIENT_SECRET": GRAPH_HOSTS}
                wd = steps["pull"][0][1]["would_do"]
                assert wd["url"] == ("https://graph.microsoft.com/v1.0/security/alerts_v2"
                                     "?$filter=createdDateTime%20ge%202026-10-18T10%3A00%3A00Z&$top=100")
                assert wd["auth"]["token_url"] == f"https://login.microsoftonline.com/{AZ_TENANT}/oauth2/v2.0/token"
                assert wd["auth"]["scope"] == "https://graph.microsoft.com/.default"
                assert "⟦secret:MS_CLIENT_SECRET#" in wd["auth"]["client_secret"]
                got = sorted((o["would_do"]["source"], o["would_do"]["external_id"], o["would_do"]["title"], o["would_do"]["severity"])
                             for _, o in steps["create"])
                assert got == [
                    ("m365-defender", "da637551227677560813_-961444813",
                     "Suspicious execution of hidden file (microsoftDefenderForEndpoint)", "low"),
                    ("m365-defender", "da637878227677560813_-334257894",
                     "Suspicious sign-in activity detected (microsoftDefenderForIdentity)", "info"),
                ], got

                # Entra ID Protection riskDetections
                steps, _ = await install_and_run("entra-risk-detections-pull", {"AZURE_TENANT_ID": AZ_TENANT},
                                                 {"payload": since, "mocks": {"pull": {"status": 200, "headers": {}, "body": ENTRA_RISKS}}})
                assert steps["pull"][0][1]["would_do"]["url"] == (
                    "https://graph.microsoft.com/v1.0/identityProtection/riskDetections"
                    "?$filter=detectedDateTime%20ge%202026-10-18T10%3A00%3A00Z&$top=200")
                got = sorted((o["would_do"]["source"], o["would_do"]["external_id"], o["would_do"]["title"], o["would_do"]["severity"])
                             for _, o in steps["create"])
                assert got == [
                    ("entra-id", "6a5874ca-abcd-9d82-5ad39bd71600", "Entra risk: unfamiliarFeatures for olack@adatum.com", "medium"),
                    ("entra-id", "7b6985db-bcde-0e93-6be40ce82711", "Entra risk: generic for rng@adatum.com", "medium"),
                ], got

                # Identity triage on a pending Defender alert with user evidence
                steps, _ = await install_and_run("identity-alerts-to-case", {"PLAYBOOK_ID": "12345"}, {"alert_id": ids["alert"]})
                assert steps["promote"][0][1]["would_do"]["group_key"] == "user:olack@adatum.com"
                assert steps["promote"][0][1]["would_do"]["severity"] == "high"
                assert steps["user"][0][1]["would_do"]["artifact_type"] == "email"
                assert steps["user"][0][1]["would_do"]["value"] == "olack@adatum.com"
                assert steps["playbook"][0][1]["would_do"]["template_id"] == "12345"

                # Okta containment
                lookup = {"status": 200, "headers": {}, "body": {"id": "00u1abcd", "status": "ACTIVE",
                                                                 "profile": {"login": "olack@adatum.com"}}}
                steps, _ = await install_and_run("okta-contain-user", {"OKTA_DOMAIN": OKTA},
                                                 {"case_id": ids["case"], "mocks": {"lookup": lookup}})
                assert steps["lookup"][0][1]["would_do"]["url"] == f"https://{OKTA}/api/v1/users/olack%40adatum.com"
                assert steps["suspend"][0][1]["would_do"]["method"] == "POST"
                assert steps["suspend"][0][1]["would_do"]["url"] == f"https://{OKTA}/api/v1/users/00u1abcd/lifecycle/suspend"
                assert steps["revoke"][0][1]["would_do"]["method"] == "DELETE"
                assert steps["revoke"][0][1]["would_do"]["url"] == f"https://{OKTA}/api/v1/users/00u1abcd/sessions?oauthTokens=true"
                assert steps["no_target"][0][0] == steps["not_found"][0][0] == "skipped"
                assert "suspend HTTP 200, revoke sessions HTTP 200" in steps["result"][0][1]["would_do"]["content"]

                # Entra containment (case tagged entra-disable -> also PATCH accountEnabled)
                steps, _ = await install_and_run("entra-revoke-sessions", {"AZURE_TENANT_ID": AZ_TENANT}, {"case_id": ids["case"]})
                rv, ds = steps["revoke"][0][1]["would_do"], steps["disable"][0][1]["would_do"]
                assert rv["method"] == "POST"
                assert rv["url"] == "https://graph.microsoft.com/v1.0/users/olack%40adatum.com/revokeSignInSessions"
                assert ds["method"] == "PATCH" and ds["url"] == "https://graph.microsoft.com/v1.0/users/olack%40adatum.com"
                assert ds["body"] == {"accountEnabled": False}
                assert "⟦secret:ENTRA_RESPONSE_CLIENT_SECRET#" in rv["auth"]["client_secret"]
                assert "revokeSignInSessions HTTP 200, disable account: 200" in steps["result"][0][1]["would_do"]["content"]
        finally:
            app.dependency_overrides.clear()
            tpl.reset_cache()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))  # runs/steps cascade
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            if ids["alert"]:
                await db.execute(delete(Alert).where(Alert.id == ids["alert"]))
            if ids["case"]:
                await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == ids["case"]))
                await db.execute(delete(Case).where(Case.id == ids["case"]))
            if ids["art"]:
                await db.execute(delete(Artifact).where(Artifact.id == ids["art"]))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_identity_templates_dry_run(monkeypatch):
    import app.tasks.workflows as tw
    queue = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: queue.append(("advance", rid)))
    monkeypatch.setattr(tw.execute_step_task, "delay", lambda sid: queue.append(("step", sid)))
    monkeypatch.setattr(tw.loop_tick_task, "delay", lambda sid: queue.append(("tick", sid)))
    asyncio.run(_scenario(queue))
