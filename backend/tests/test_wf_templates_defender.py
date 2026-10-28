"""Microsoft Defender for Endpoint response templates (isolate, release): install through the API, dry-run with mocks
shaped like the MDE API (machines list, machine action), assert requests, token scope, secrets and outputs."""
import asyncio
import json
import secrets

import httpx
from sqlalchemy import delete

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
from app.models.workflow import Workflow
from app.utils.crypto import encrypt
from app.workflows import templates as tpl
from tests.test_wf_templates_atlassian import _top
from tests.test_wf_templates_edr import _drain, _queue

AZ = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
HOST = "eu.api.security.microsoft.com"
PARAMS = {"AZURE_TENANT_ID": AZ, "DEFENDER_API_HOST": HOST}


def ok(body, status=200):
    return {"status": status, "headers": {}, "body": body}


# GET /api/machines?$filter=... -> {"@odata.context", "value": [machine]}; POST .../isolate -> 201 MachineAction
ONE = ok({"value": [{"id": "1e5bc9d7e413ddd7902c2932e418702b84d0cc07", "computerDnsName": "fin-lt-07.acme.com"}]})
NONE = ok({"value": []})
ACTION = ok({"id": "b89eb834-4578-496c-8be0-03f004061435", "type": "Isolate", "status": "Pending"}, 201)
# Graph alerts_v2 alert with deviceEvidence, as stored by the m365-defender pull template
DEF_ALERT = {"id": "da6378", "title": "Ransomware behavior", "serviceSource": "microsoftDefenderForEndpoint", "evidence": [
    {"@odata.type": "#microsoft.graph.security.userEvidence", "userAccount": {"userPrincipalName": "a@acme.com"}},
    {"@odata.type": "#microsoft.graph.security.deviceEvidence", "deviceDnsName": "HR-LT-02.acme.com",
     "mdeDeviceId": "2b5bc9d7e413ddd7902c2932e418702b84d0cc99"}]}


async def _scenario(q):
    h = secrets.token_hex(3)
    ids = {"wf": [], "sec": [], "case": [], "art": [], "ca": [], "atd": [], "alert": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-mde", slug=f"wf-mde-{h}")
        u = User(email=f"wf-mde-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.commit()
        tid, uid = t.id, u.id
        try:
            for name in ("DEFENDER_CLIENT_ID", "DEFENDER_CLIENT_SECRET"):
                s = TenantSecret(tenant_id=tid, name=name, value_enc=encrypt(f"val-{name}-{h}"),
                                 allowed_hosts=["login.microsoftonline.com", HOST])
                db.add(s)
                await db.flush()
                ids["sec"].append(s.id)
            atd = ArtifactTypeDefinition(tenant_id=tid, key="hostname", label="Hostname")
            case, from_alert, bare = (Case(title="Beacon from FIN-LT-07", tenant_id=tid, tags=[]),
                                      Case(title="Defender alert case", tenant_id=tid, tags=[]),
                                      Case(title="No device", tenant_id=tid, tags=[]))
            db.add_all([atd, case, from_alert, bare])
            await db.flush()
            ids["atd"].append(atd.id)
            ids["case"] += [case.id, from_alert.id, bare.id]
            art = Artifact(tenant_id=tid, artifact_type=ArtifactType.OTHER, value="FIN-LT-07.acme.com", custom_type_id=atd.id)
            alert = Alert(tenant_id=tid, source="m365-defender", external_id="da6378", title="t", status="promoted",
                          severity="high", payload=DEF_ALERT, case_id=from_alert.id)
            db.add_all([art, alert])
            await db.flush()
            ids["art"].append(art.id)
            ids["alert"].append(alert.id)
            ca = CaseArtifact(case_id=case.id, artifact_id=art.id)
            db.add(ca)
            await db.commit()
            ids["ca"].append(ca.id)
            case_id, alert_case_id, bare_id = case.id, from_alert.id, bare.id

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
                    run, top, _ = await _top(r.json()["run_id"])
                    assert run.status == "succeeded", (run.error, {k: (s.status, s.error) for k, s in top.items()})
                    return top

                def authed(w):
                    a = w["auth"]
                    assert a["token_url"] == f"https://login.microsoftonline.com/{AZ}/oauth2/v2.0/token", a
                    assert a["scope"] == "https://api.securitycenter.microsoft.com/.default"  # 403 with any other audience
                    assert "val-" not in json.dumps(w)

                api = f"https://{HOST}/api/machines"
                mid = ONE["body"]["value"][0]["id"]
                for key, action in (("defender-isolate-device", "isolate"), ("defender-release-device", "unisolate")):
                    wf = await install(key)
                    top = await dry(wf, case_id=case_id, mocks={"find": ONE, action: ACTION})
                    fw = top["find"].output["would_do"]
                    assert fw["url"] == f"{api}?$filter=computerDnsName%20eq%20%27fin-lt-07.acme.com%27&$top=2", fw["url"]
                    authed(fw)
                    aw = top[action].output["would_do"]
                    assert aw["method"] == "POST" and aw["url"] == f"{api}/{mid}/{action}", aw["url"]
                    assert aw["body"]["Comment"] == f"SOC Hub case #{case_id}: Beacon from FIN-LT-07"
                    assert aw["body"].get("IsolationType") == ("Full" if action == "isolate" else None)
                    authed(aw)
                    tr = top["task_done"].output["would_do"]
                    assert tr["result_status"] == "ok" and tr["complete"] in ("True", True), tr
                    assert "accepted, machine action b89eb834" in tr["result"], tr["result"]
                    assert ("guard" in top) == (action == "isolate")
                    if action == "isolate":
                        assert top["guard"].output["allowed"] is True

                    # device from the promoted Defender alert's deviceEvidence
                    top = await dry(wf, case_id=alert_case_id, mocks={"find": ONE, action: ACTION})
                    assert "computerDnsName%20eq%20%27hr-lt-02.acme.com%27" in top["find"].output["would_do"]["url"]
                    # no exact match, refused action, no device on the case
                    top = await dry(wf, case_id=case_id, mocks={"find": NONE})
                    assert top[action].status == "skipped" and top["task_not_found"].output["would_do"]["result_status"] == "failed"
                    assert "0 devices with that exact name" in top["not_found"].output["would_do"]["content"]
                    top = await dry(wf, case_id=case_id, mocks={"find": ONE, action: ok({"error": {"code": "Forbidden"}}, 403)})
                    assert top["task_done"].output["would_do"]["result_status"] == "failed"
                    assert "not accepted: check the Machine.Isolate permission" in top["done"].output["would_do"]["content"]
                    top = await dry(wf, case_id=bare_id)
                    assert top["find"].status == "skipped" and top["task_no_host"].output["would_do"]["result_status"] == "failed"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))
            await db.execute(delete(CaseArtifact).where(CaseArtifact.id.in_(ids["ca"])))
            await db.execute(delete(Artifact).where(Artifact.id.in_(ids["art"])))
            await db.execute(delete(ArtifactTypeDefinition).where(ArtifactTypeDefinition.id.in_(ids["atd"])))
            await db.execute(delete(Alert).where(Alert.id.in_(ids["alert"])))
            await db.execute(delete(Case).where(Case.id.in_(ids["case"])))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_defender_response_templates_dry_run(monkeypatch):
    asyncio.run(_scenario(_queue(monkeypatch)))
