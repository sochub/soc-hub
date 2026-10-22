"""Google Workspace templates: install through the API into a throwaway tenant, dry-run with doc-shaped
Alert Center responses, assert request URL, google_service_account auth shape and alert_create inputs;
the response template with/without a target and with/without the `gws-suspend` tag.

Mock bodies follow https://developers.google.com/workspace/admin/alertcenter/reference/rest/v1beta1/alerts
(alerts.list → {alerts: [Alert], nextPageToken}; AccountWarning / StateSponsoredAttack data carry `email`).
Runs are driven in-process (Celery .delay calls are queued and drained here), so no worker is needed.
"""
import asyncio
import json
import secrets

import httpx
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.utils.crypto import encrypt
from app.workflows import templates as tpl
from tests.test_wf_templates_edr import _drain, _queue

ADMIN = "secops-reader@acme.example"
SCHEDULE = {"event": "schedule", "fired_at": "2026-10-18T10:05:00Z", "since": "2026-10-18T10:00:00Z",
            "case_id": None, "alert_id": None, "changes": {}}
ALERTS = {"alerts": [
    {"customerId": "01abcde2", "alertId": "e3b1f6a0-8d2c-4f3e-9a51-0c7b2d4e6f81",
     "createTime": "2026-10-18T10:01:12.345Z", "startTime": "2026-10-18T09:58:00Z", "updateTime": "2026-10-18T10:01:12.345Z",
     "type": "Government attack warning", "source": "State Sponsored Attack",
     "data": {"@type": "type.googleapis.com/google.apps.alertcenter.type.StateSponsoredAttack", "email": "olack@acme.example"},
     "metadata": {"customerId": "01abcde2", "alertId": "e3b1f6a0-8d2c-4f3e-9a51-0c7b2d4e6f81", "status": "NOT_STARTED",
                  "severity": "HIGH", "updateTime": "2026-10-18T10:01:12.345Z", "etag": "W/\"1\""},
     "etag": "W/\"1\""},
    {"customerId": "01abcde2", "alertId": "5c9d2e10-1f3a-4b7c-8e2d-6a0b9c8d7e12",
     "createTime": "2026-10-18T10:03:40Z", "startTime": "2026-10-18T10:02:00Z",
     "type": "Suspicious login", "source": "Google identity",
     "data": {"@type": "type.googleapis.com/google.apps.alertcenter.type.AccountWarning", "email": "rng@acme.example",
              "loginDetails": {"loginTime": "2026-10-18T10:01:59Z", "ipAddress": "203.0.113.7"}},
     "metadata": {"status": "NOT_STARTED", "severity": "MEDIUM"}},
    {"customerId": "01abcde2", "alertId": "9a8b7c6d-0000-4e3f-9a51-111111111111",
     "createTime": "2026-10-18T10:04:00Z", "type": "Activity Rule", "source": "Security Center rules",
     "data": {"@type": "type.googleapis.com/google.apps.alertcenter.type.ActivityRule", "name": "Mass file download"},
     "metadata": {"severity": "LOW"}},
], "nextPageToken": ""}


def ok(body, status=200):
    return {"status": status, "headers": {}, "body": body}


async def _run(run_id):
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        top = {s.node_id: s for s in (await db.execute(
            select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))).scalars()}
        kid_ids = (await db.execute(select(WorkflowRun.id).where(WorkflowRun.parent_run_id == run_id))).scalars().all()
        creates = [s.output["would_do"] for s in (await db.execute(select(WorkflowRunStep).where(
            WorkflowRunStep.run_id.in_(kid_ids or [-1]), WorkflowRunStep.node_id == "create"))).scalars()]
        return run, top, sorted((c["source"], c["external_id"], c["title"], c["severity"]) for c in creates)


def _no_secret_values(obj):
    assert "val-" not in json.dumps(obj, default=str), obj


async def _scenario(q):
    h = secrets.token_hex(3)
    ids = {"wf": [], "case": [], "art": None, "sec": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-gws", slug=f"wf-gws-{h}")
        u = User(email=f"wf-gws-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.flush()
        tid, uid = t.id, u.id
        try:
            for name, hosts in (("GWS_SA_KEY", ["oauth2.googleapis.com", "alertcenter.googleapis.com"]),
                                ("GWS_RESPONSE_SA_KEY", ["oauth2.googleapis.com", "admin.googleapis.com"])):
                s = TenantSecret(tenant_id=tid, name=name, value_enc=encrypt(f"val-{name}-{h}"), allowed_hosts=hosts)
                db.add(s)
                await db.flush()
                ids["sec"].append(s.id)
            art = Artifact(tenant_id=tid, artifact_type=ArtifactType.EMAIL, value="olack@acme.example")
            db.add(art)
            await db.flush()
            ids["art"] = art.id
            cases = {}
            for label, tags, with_art in (("tagged", ["gws-suspend"], True), ("untagged", [], True), ("empty", ["gws-suspend"], False)):
                c_ = Case(title=f"GWS {label}", tenant_id=tid, tags=tags)
                db.add(c_)
                await db.flush()
                ids["case"].append(c_.id)
                cases[label] = c_.id
                if with_art:
                    db.add(CaseArtifact(case_id=c_.id, artifact_id=art.id))
            await db.commit()

            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.require_admin] = lambda: u
            tpl.reset_cache()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                async def install(key, admin=ADMIN):
                    r = await c.post(f"/api/v1/workflows/templates/{key}/install", json={"params": {"ADMIN_EMAIL": admin}})
                    assert r.status_code == 201, r.text
                    d = r.json()
                    ids["wf"].append(d["workflow"]["id"])
                    assert d["workflow"]["validation_errors"] in (None, []), d["workflow"]
                    assert d["missing_secrets"] == [], d
                    return d

                async def dry(wf_id, expect="succeeded", **body):
                    r = await c.post(f"/api/v1/workflows/{wf_id}/dry-run", json=body)
                    assert r.status_code == 200, r.text
                    await _drain(q)
                    run, top, creates = await _run(r.json()["run_id"])
                    assert run.status == expect, (run.error, {k: (s.status, s.error) for k, s in top.items()})
                    return top, creates

                # Bad email param rejected at install
                r = await c.post("/api/v1/workflows/templates/google-workspace-alerts-pull/install",
                                 json={"params": {"ADMIN_EMAIL": "not an email"}})
                assert r.status_code == 422, r.text

                # ── Alert Center pull ──
                d = await install("google-workspace-alerts-pull")
                assert d["secret_hosts"] == {"GWS_SA_KEY": ["oauth2.googleapis.com", "alertcenter.googleapis.com"]}
                wf = d["workflow"]["id"]
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": ok(ALERTS)})
                pw = top["pull"].output["would_do"]
                assert pw["method"] == "GET" and pw["url"] == (
                    "https://alertcenter.googleapis.com/v1beta1/alerts"
                    "?filter=createTime%20%3E%3D%20%222026-10-18T10%3A00%3A00Z%22&orderBy=createTime%20desc&pageSize=100"), pw["url"]
                a = pw["auth"]
                assert a["type"] == "google_service_account" and a["subject"] == ADMIN
                assert a["scopes"] == "https://www.googleapis.com/auth/apps.alerts"
                assert a["service_account_key"].startswith("⟦secret:GWS_SA_KEY#"), a
                _no_secret_values(pw)
                assert creates == [
                    ("google-workspace", "5c9d2e10-1f3a-4b7c-8e2d-6a0b9c8d7e12", "Suspicious login (rng@acme.example)", "medium"),
                    ("google-workspace", "9a8b7c6d-0000-4e3f-9a51-111111111111", "Activity Rule", "low"),
                    ("google-workspace", "e3b1f6a0-8d2c-4f3e-9a51-0c7b2d4e6f81", "Government attack warning (olack@acme.example)", "high"),
                ], creates

                # No alerts in the window: the API omits `alerts` → nothing created, run succeeds
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": ok({})})
                assert creates == [] and top["failed"].status == "skipped"

                # 403 → failed run with the status only, never the body
                top, creates = await dry(wf, expect="failed", payload=SCHEDULE, mocks={"pull": ok(
                    {"error": {"code": 403, "message": "Caller val-leak does not have permission", "status": "PERMISSION_DENIED"}}, 403)})
                assert creates == [] and top["failed"].status == "failed"
                assert top["failed"].error == "Google Workspace alerts pull failed: HTTP 403", top["failed"].error

                # ── Containment ──
                d = await install("google-workspace-suspend-user", admin="gws-responder@acme.example")
                assert d["secret_hosts"] == {"GWS_RESPONSE_SA_KEY": ["oauth2.googleapis.com", "admin.googleapis.com"]}
                wf = d["workflow"]["id"]

                # target + gws-suspend tag → signOut and suspend
                top, _ = await dry(wf, case_id=cases["tagged"])
                so, su = top["signout"].output["would_do"], top["suspend"].output["would_do"]
                assert so["method"] == "POST"
                assert so["url"] == "https://admin.googleapis.com/admin/directory/v1/users/olack%40acme.example/signOut"
                assert so["auth"]["scopes"] == "https://www.googleapis.com/auth/admin.directory.user.security"
                assert so["auth"]["subject"] == "gws-responder@acme.example"
                assert so["auth"]["service_account_key"].startswith("⟦secret:GWS_RESPONSE_SA_KEY#")
                assert su["method"] == "PUT" and su["url"] == "https://admin.googleapis.com/admin/directory/v1/users/olack%40acme.example"
                assert su["body"] == {"suspended": True}
                assert su["auth"]["scopes"] == "https://www.googleapis.com/auth/admin.directory.user"
                _no_secret_values([so, su])
                assert top["no_target"].status == "skipped"
                assert ("Google Workspace containment for olack@acme.example: signOut HTTP 200, suspend: 200."
                        in top["result"].output["would_do"]["content"])

                # target, no tag → signOut only
                top, _ = await dry(wf, case_id=cases["untagged"])
                assert top["signout"].status == "succeeded" and top["suspend"].status == "skipped"
                assert "signOut HTTP 200, suspend: not requested." in top["result"].output["would_do"]["content"]

                # no target (tag set) → note only, no Google call
                top, _ = await dry(wf, case_id=cases["empty"])
                assert top["no_target"].status == "succeeded"
                assert "no target user" in top["no_target"].output["would_do"]["content"]
                assert all(top[n].status == "skipped" for n in ("signout", "suspend", "result") if n in top), \
                    {k: s.status for k, s in top.items()}
        finally:
            app.dependency_overrides.clear()
            tpl.reset_cache()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))  # runs and steps cascade
            await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(ids["case"])))
            await db.execute(delete(Case).where(Case.id.in_(ids["case"])))
            if ids["art"]:
                await db.execute(delete(Artifact).where(Artifact.id == ids["art"]))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))  # throwaway tenant's own rows
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_gws_templates_dry_run(monkeypatch):
    asyncio.run(_scenario(_queue(monkeypatch)))
