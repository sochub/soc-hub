"""Atlassian organization admin templates (audit-log pull, suspend user): install through the API, dry-run with
mocks shaped like the Organization REST API's OpenAPI schemas, assert requests, secret placeholders and outputs."""
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

ORG = "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
ADMIN = f"https://api.atlassian.com/admin"
SCHEDULE = {"event": "schedule", "fired_at": "2026-10-18T10:05:00+00:00", "since": "2026-10-18T10:00:00Z",
            "since_ms": 1792317600000, "case_id": None, "alert_id": None, "changes": {}}


def ok(body, status=200):
    return {"status": status, "headers": {}, "body": body}


# GET /v1/orgs/{orgId}/events-stream: {data: [{id, type, attributes: {time, action, actor, location...}}], meta, links}
EVENTS = ok({"data": [
    {"id": "e-0001", "type": "events", "attributes": {
        "time": "2026-10-18T10:01:00Z", "action": "user_login_failed",
        "actor": {"id": "557058:abc", "name": "Alice", "email": "alice@acme.com"},
        "location": {"ip": "203.0.113.5", "countryName": "Nigeria", "city": "Lagos"}}, "links": {"self": "x"}},
    {"id": "e-0002", "type": "events", "attributes": {
        "time": "2026-10-18T10:02:00Z", "action": "api_key_created", "actor": {"id": "557058:def", "name": "Bob"}}},
], "meta": {"next": None, "page_size": 2}, "links": {"self": "x"}})
SEARCH = ok({"data": [{"accountId": "557058:abc", "email": "alice@acme.com", "name": "Alice", "status": "active"}],
             "links": {"self": "x"}})
DIRS = ok({"data": [{"directoryId": "dir-1", "name": "Acme"}], "links": {"self": "x"}})


async def _top(run_id):
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        top = {s.node_id: s for s in (await db.execute(
            select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))).scalars()}
        kids = (await db.execute(select(WorkflowRun.id).where(WorkflowRun.parent_run_id == run_id))).scalars().all()
        creates = [s.output["would_do"] for s in (await db.execute(select(WorkflowRunStep).where(
            WorkflowRunStep.run_id.in_(kids or [-1]), WorkflowRunStep.node_id == "create"))).scalars()
            if s.output and "would_do" in s.output]
        return run, top, sorted((c["source"], c["external_id"], c["title"], c["severity"]) for c in creates)


async def _scenario(q):
    h = secrets.token_hex(3)
    ids = {"wf": [], "sec": [], "case": [], "art": [], "ca": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-atl", slug=f"wf-atl-{h}")
        u = User(email=f"wf-atl-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.commit()
        tid, uid = t.id, u.id
        try:
            s = TenantSecret(tenant_id=tid, name="ATLASSIAN_ADMIN_API_KEY", value_enc=encrypt(f"val-atl-{h}"),
                             allowed_hosts=["api.atlassian.com"])
            case, bare = Case(title="Suspicious Jira login", tenant_id=tid, tags=[]), Case(title="No user", tenant_id=tid, tags=[])
            db.add_all([s, case, bare])
            await db.flush()
            ids["sec"].append(s.id)
            ids["case"] += [case.id, bare.id]
            art = Artifact(tenant_id=tid, artifact_type=ArtifactType.EMAIL, value="alice@acme.com")
            db.add(art)
            await db.flush()
            ids["art"].append(art.id)
            ca = CaseArtifact(case_id=case.id, artifact_id=art.id)
            db.add(ca)
            await db.commit()
            ids["ca"].append(ca.id)
            case_id, bare_id = case.id, bare.id

            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.require_admin] = lambda: u
            tpl.reset_cache()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                async def install(key):
                    r = await c.post(f"/api/v1/workflows/templates/{key}/install", json={"params": {"ATLASSIAN_ORG_ID": ORG}})
                    assert r.status_code == 201, r.text
                    d = r.json()
                    ids["wf"].append(d["workflow"]["id"])
                    assert d["missing_secrets"] == [] and d["secret_hosts"] == {"ATLASSIAN_ADMIN_API_KEY": ["api.atlassian.com"]}, d
                    return d["workflow"]["id"]

                async def dry(wf_id, expect="succeeded", **body):
                    r = await c.post(f"/api/v1/workflows/{wf_id}/dry-run", json=body)
                    assert r.status_code == 200, r.text
                    await _drain(q)
                    run, top, creates = await _top(r.json()["run_id"])
                    assert run.status == expect, (run.error, {k: (s.status, s.error) for k, s in top.items()})
                    return top, creates

                def bearer(w):
                    assert w["headers"]["Authorization"].startswith("Bearer ⟦secret:ATLASSIAN_ADMIN_API_KEY#"), w["headers"]
                    assert "val-" not in json.dumps(w)

                # ── audit-log pull ──
                wf = await install("atlassian-audit-log-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": EVENTS})
                pw = top["pull"].output["would_do"]
                assert pw["method"] == "GET" and pw["url"] == (
                    f"{ADMIN}/v1/orgs/{ORG}/events-stream?from=1792317600000&limit=200&sortOrder=asc"), pw["url"]
                bearer(pw)
                assert creates == [
                    ("atlassian", "e-0001", "user_login_failed by alice@acme.com", "info"),
                    ("atlassian", "e-0002", "api_key_created by Bob", "info"),
                ], creates
                top, _ = await dry(wf, expect="failed", payload=SCHEDULE, mocks={"pull": ok({"code": 401, "message": "val-x"}, 401)})
                assert top["failed"].error == "Atlassian audit-log pull failed: HTTP 401"

                # ── suspend user ──
                wf = await install("atlassian-suspend-user")
                top, _ = await dry(wf, case_id=case_id, mocks={"find": SEARCH, "dirs": DIRS, "suspend": ok(None, 204)})
                assert top["guard"].status == "succeeded" and top["guard"].output["allowed"] is True
                fw = top["find"].output["would_do"]
                assert fw["method"] == "POST" and fw["url"] == f"{ADMIN}/v2/orgs/{ORG}/directories/-/users/search"
                assert fw["body"] == {"emails": ["alice@acme.com"], "limit": 2}, fw["body"]
                bearer(fw)
                dw = top["dirs"].output["would_do"]
                assert dw["url"] == f"{ADMIN}/v2/orgs/{ORG}/directories?accountId=557058%3Aabc", dw["url"]
                sw = top["suspend"].output["would_do"]
                assert sw["method"] == "POST" and sw["url"] == (
                    f"{ADMIN}/v2/orgs/{ORG}/directories/dir-1/users/557058%3Aabc/suspend"), sw["url"]
                bearer(sw)
                assert top["result"].output["would_do"]["content"] == (
                    "Atlassian suspend for alice@acme.com: search HTTP 200, suspend HTTP 204 (1 directory/ies; the first was used).")
                tr = top["task_result"].output["would_do"]
                assert tr["result_status"] == "ok" and tr["complete"] in ("True", True)
                # no match, and no user on the case
                top, _ = await dry(wf, case_id=case_id, mocks={"find": ok({"data": [], "links": {}})})
                assert top["suspend"].status == "skipped" and top["not_found"].status == "succeeded"
                assert top["task_not_found"].output["would_do"]["result_status"] == "failed"
                top, _ = await dry(wf, case_id=bare_id)
                assert top["guard"].status == "skipped" and top["no_target"].status == "succeeded"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))
            await db.execute(delete(CaseArtifact).where(CaseArtifact.id.in_(ids["ca"])))
            await db.execute(delete(Artifact).where(Artifact.id.in_(ids["art"])))
            await db.execute(delete(Case).where(Case.id.in_(ids["case"])))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_atlassian_templates_dry_run(monkeypatch):
    asyncio.run(_scenario(_queue(monkeypatch)))
