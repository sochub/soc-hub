"""Cloudflare templates (WAF top IPs, audit logs, Security Center insights, block IP): install through the API,
dry-run with documentation-shaped mocks, assert requests, secret placeholders and alert_create inputs.

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

SINCE = "2026-10-18T10:00:00Z"
SCHEDULE = {"event": "schedule", "fired_at": "2026-10-18T10:05:00.123456+00:00", "since": SINCE,
            "case_id": None, "alert_id": None, "changes": {}}
ACCOUNT, ZONE = "023e105f4ecef8ad9ca31a8372d0c353", "9a7806061c88ada191ed06f989cc3dac"
PARAMS = {"ACCOUNT_ID": ACCOUNT, "ZONE_ID": ZONE, "MIN_EVENTS": "100"}
API = "https://api.cloudflare.com/client/v4"


def ok(body, status=200):
    return {"status": status, "headers": {}, "body": body}


# GraphQL Analytics: firewallEventsAdaptiveGroups grouped by client IP (errors is null on success)
WAF = ok({"data": {"viewer": {"zones": [{"firewallEventsAdaptiveGroups": [
    {"count": 1520, "dimensions": {"clientIP": "203.0.113.69", "clientCountryName": "NL", "clientAsn": "64496",
                                   "clientASNDescription": "EXAMPLE-AS"}},
    {"count": 310, "dimensions": {"clientIP": "2001:db8::1", "clientCountryName": "US", "clientAsn": "64511",
                                  "clientASNDescription": "DOC-AS"}},
    {"count": 99, "dimensions": {"clientIP": "198.51.100.7", "clientCountryName": "DE", "clientAsn": "64500",
                                 "clientASNDescription": "LOW"}},  # below MIN_EVENTS: skipped
]}]}}, "errors": None})
# Audit Logs v2 (GET /accounts/{id}/logs/audit), shape from the API reference
AUDIT = ok({"success": True, "errors": [], "result_info": {"count": "3", "cursor": "ASqdKd7d"}, "result": [
    {"id": "f174be97-19b1-40d6-954d-70cd5fbd52db", "account": {"id": ACCOUNT, "name": "Example Account"},
     "action": {"description": "Add Member", "result": "success", "time": "2026-10-18T10:01:07Z", "type": "create"},
     "actor": {"id": "f6b5de0326bb5182b8a4840ee01ec774", "context": "dash", "email": "alice@example.com",
               "ip_address": "198.41.129.166", "type": "user"},
     "raw": {"cf_ray_id": "8e9b1c60ef9e1c9a", "method": "POST", "status_code": 200, "uri": f"/accounts/{ACCOUNT}/members",
             "user_agent": "Mozilla/5.0"},
     "resource": {"id": "m1", "product": "members", "request": {}, "response": {}, "scope": {}, "type": "member"}},
    {"id": "a2b3c4d5-0000-40d6-954d-70cd5fbd0002", "account": {"id": ACCOUNT},
     "action": {"description": "Update ruleset", "result": "success", "time": "2026-10-18T10:02:00Z", "type": "update"},
     "actor": {"id": "x", "context": "api_token", "token_id": "144cdb2e", "token_name": "terraform", "type": "user"},
     "raw": {"method": "PUT", "status_code": 200, "uri": f"/zones/{ZONE}/rulesets/phases/http_request_firewall_custom/entrypoint"},
     "resource": {"product": "rulesets"}, "zone": {"id": ZONE, "name": "example.com"}},
    {"id": "b3c4d5e6-0000-40d6-954d-70cd5fbd0003", "account": {"id": ACCOUNT},
     "action": {"description": "Create DNS record", "result": "success", "time": "2026-10-18T10:03:00Z", "type": "create"},
     "actor": {"email": "bob@example.com", "type": "user"},
     "raw": {"method": "POST", "status_code": 200, "uri": f"/zones/{ZONE}/dns_records"},
     "resource": {"product": "dns_records"}, "zone": {"name": "example.com"}},  # not security relevant: skipped
]})
# Security Center insights (GET /accounts/{id}/security-center/insights)
INSIGHTS = ok({"success": True, "errors": [], "messages": [], "result": {"count": 3, "page": 1, "per_page": 500, "issues": [
    {"id": "1f0e0d0c-aaaa-4bbb-8ccc-000000000001", "dismissed": False, "issue_class": "exposed_infrastructure_dangling_record",
     "issue_type": "exposed_infrastructure", "severity": "Critical", "status": "active", "subject": "old.example.com",
     "since": "2026-10-17T03:00:00Z", "timestamp": "2026-10-18T03:00:00Z", "source": "cloudflare",
     "resolve_text": "Remove the record", "resolve_link": "https://dash.cloudflare.com/", "user_classification": None,
     "payload": {"detection_method": "DNS record points to an unclaimed resource", "zone_tag": ZONE}},
    {"id": "1f0e0d0c-aaaa-4bbb-8ccc-000000000002", "dismissed": False, "issue_class": "always_use_https_not_enabled",
     "issue_type": "insecure_configuration", "severity": "Moderate", "status": "active", "subject": "example.com",
     "user_classification": None, "payload": {}},
    {"id": "1f0e0d0c-aaaa-4bbb-8ccc-000000000003", "dismissed": False, "issue_class": "dnssec_not_enabled",
     "issue_type": "insecure_configuration", "severity": "Low", "status": "resolved", "subject": "example.org",
     "user_classification": None, "payload": {}},  # resolved: skipped
]}})


async def _run(run_id):
    """(run, {node_id: step} for top-level steps, sorted would_do tuples of every create step that ran in a loop child)"""
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        top = {s.node_id: s for s in (await db.execute(
            select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))).scalars()}
        kid_ids = (await db.execute(select(WorkflowRun.id).where(WorkflowRun.parent_run_id == run_id))).scalars().all()
        steps = (await db.execute(select(WorkflowRunStep).where(
            WorkflowRunStep.run_id.in_(kid_ids or [-1]), WorkflowRunStep.node_id == "create"))).scalars().all()
        creates = [s.output["would_do"] for s in steps if s.output and "would_do" in s.output]
        return run, top, sorted((c["source"], c["external_id"], c["title"], c["severity"]) for c in creates)


def _no_secret_values(obj):
    assert "val-" not in json.dumps(obj, default=str), obj


async def _scenario(q):
    h = secrets.token_hex(3)
    ids = {"wf": [], "sec": [], "case": [], "art": [], "ca": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-cf", slug=f"wf-cf-{h}")
        u = User(email=f"wf-cf-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.commit()
        tid, uid = t.id, u.id
        try:
            s = TenantSecret(tenant_id=tid, name="CLOUDFLARE_API_TOKEN", value_enc=encrypt(f"val-cf-{h}"),
                             allowed_hosts=["api.cloudflare.com"])
            db.add(s)
            await db.flush()
            ids["sec"].append(s.id)
            case = Case(title="Credential stuffing from 203.0.113.69", tenant_id=tid, tags=[])
            bare = Case(title="No IP here", tenant_id=tid, tags=[])
            db.add_all([case, bare])
            await db.flush()
            ids["case"] += [case.id, bare.id]
            art = Artifact(tenant_id=tid, artifact_type=ArtifactType.IP, value="203.0.113.69")
            db.add(art)
            await db.flush()
            ids["art"].append(art.id)
            ca = CaseArtifact(case_id=case.id, artifact_id=art.id)
            db.add(ca)
            await db.flush()
            ids["ca"].append(ca.id)
            await db.commit()
            case_id, bare_id = case.id, bare.id

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
                    assert d["missing_secrets"] == [] and d["secret_hosts"] == {"CLOUDFLARE_API_TOKEN": ["api.cloudflare.com"]}, d
                    return d["workflow"]["id"]

                async def dry(wf_id, expect="succeeded", **body):
                    r = await c.post(f"/api/v1/workflows/{wf_id}/dry-run", json=body)
                    assert r.status_code == 200, r.text
                    await _drain(q)
                    run, top, creates = await _run(r.json()["run_id"])
                    assert run.status == expect, (run.error, {k: (s.status, s.error) for k, s in top.items()})
                    return top, creates

                async def failed_with(wf_id, message, mocks):
                    top, creates = await dry(wf_id, expect="failed", payload=SCHEDULE, mocks=mocks)
                    assert creates == [] and top["failed"].status == "failed" and top["failed"].error == message, \
                        {k: (s.status, s.error) for k, s in top.items()}
                    assert "val-" not in (top["failed"].error or "")

                def bearer(w):
                    assert w["headers"]["Authorization"].startswith("Bearer ⟦secret:CLOUDFLARE_API_TOKEN#"), w["headers"]
                    _no_secret_values(w)

                # ── WAF top IPs (GraphQL) ──
                wf = await install("cloudflare-waf-top-ips-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": WAF})
                pw = top["pull"].output["would_do"]
                assert pw["method"] == "POST" and pw["url"] == f"{API}/graphql"
                bearer(pw)
                assert pw["headers"]["Content-Type"] == "application/json"
                assert "firewallEventsAdaptiveGroups(filter: {datetime_geq: $start, datetime_lt: $end, " \
                       "action_in: [\"block\", \"challenge\", \"js_challenge\", \"managed_challenge\"]}, limit: 100, " \
                       "orderBy: [count_DESC])" in pw["body"]["query"]
                assert "dimensions { clientIP clientCountryName clientAsn clientASNDescription }" in pw["body"]["query"]
                # window = previous run -> this run, both 5 min back
                assert pw["body"]["variables"] == {"zoneTag": ZONE, "start": "2026-10-18T09:55:00Z", "end": "2026-10-18T10:00:00Z"}
                assert creates == [
                    ("cloudflare-waf", f"{ZONE}:2001:db8::1:2026-10-18T09:55:00Z",
                     "Cloudflare WAF: 310 blocked/challenged requests from 2001:db8::1 (US)", "medium"),
                    ("cloudflare-waf", f"{ZONE}:203.0.113.69:2026-10-18T09:55:00Z",
                     "Cloudflare WAF: 1520 blocked/challenged requests from 203.0.113.69 (NL)", "high"),
                ], creates
                # previous success 12 h ago, across midnight and a month boundary: start clamped to 6 h before the end
                late = {**SCHEDULE, "since": "2026-09-30T20:00:00Z", "fired_at": "2026-10-01T02:05:30Z"}
                top, creates = await dry(wf, payload=late, mocks={"pull": WAF})
                v = top["pull"].output["would_do"]["body"]["variables"]
                assert (v["start"], v["end"]) == ("2026-09-30T20:00:30Z", "2026-10-01T02:00:30Z"), v
                assert creates[0][1].endswith(":2026-09-30T20:00:30Z")
                await failed_with(wf, "Cloudflare WAF pull failed: HTTP 200 (GraphQL errors)", {"pull": ok(
                    {"data": None, "errors": [{"message": "zones [val-x] are not authorized", "path": ["viewer", "zones", "0"]}]})})
                await failed_with(wf, "Cloudflare WAF pull failed: HTTP 403", {"pull": ok(
                    {"data": None, "errors": [{"message": "not authorized for that account val-x"}]}, 403)})

                # ── audit logs v2 ──
                wf = await install("cloudflare-audit-logs-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": AUDIT})
                pw = top["pull"].output["would_do"]
                assert pw["method"] == "GET" and pw["url"] == (
                    f"{API}/accounts/{ACCOUNT}/logs/audit?since=2026-10-18T10%3A00%3A00Z&before=2026-10-18T10%3A05%3A00Z"
                    "&direction=asc&limit=200&action_type.not=view"), pw["url"]
                bearer(pw)
                assert creates == [
                    ("cloudflare-audit", "a2b3c4d5-0000-40d6-954d-70cd5fbd0002",
                     "Cloudflare audit: Update ruleset by terraform on example.com", "medium"),
                    ("cloudflare-audit", "f174be97-19b1-40d6-954d-70cd5fbd52db",
                     "Cloudflare audit: Add Member by alice@example.com", "high"),
                ], creates
                await failed_with(wf, "Cloudflare audit log pull failed: HTTP 403", {"pull": ok(
                    {"success": False, "errors": [{"code": 10000, "message": "Authentication error val-x"}], "result": None}, 403)})

                # ── Security Center insights ──
                wf = await install("cloudflare-security-insights-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": INSIGHTS})
                pw = top["pull"].output["would_do"]
                assert pw["method"] == "GET" and pw["url"] == (
                    f"{API}/accounts/{ACCOUNT}/security-center/insights?dismissed=false"
                    "&issue_type~neq=configuration_suggestion&per_page=500")
                bearer(pw)
                assert creates == [
                    ("cloudflare-security-center", "1f0e0d0c-aaaa-4bbb-8ccc-000000000001",
                     "Cloudflare Security Center: exposed infrastructure dangling record on old.example.com", "critical"),
                    ("cloudflare-security-center", "1f0e0d0c-aaaa-4bbb-8ccc-000000000002",
                     "Cloudflare Security Center: always use https not enabled on example.com", "medium"),
                ], creates
                await failed_with(wf, "Cloudflare Security Center pull failed: HTTP 403", {"pull": ok(
                    {"success": False, "errors": [{"code": 9109, "message": "Unauthorized to access requested resource"}]}, 403)})

                # ── block IP (manual, case context) ──
                wf = await install("cloudflare-block-ip")
                created = ok({"success": True, "errors": [], "messages": [], "result": {
                    "id": "92f17202ed8bd63d69a66b86a49a8f6b", "mode": "block", "notes": "x",
                    "configuration": {"target": "ip", "value": "203.0.113.69"}, "allowed_modes": ["block"]}})
                top, _ = await dry(wf, case_id=case_id, mocks={"block": created})
                bw = top["block"].output["would_do"]
                assert bw["method"] == "POST" and bw["url"] == f"{API}/accounts/{ACCOUNT}/firewall/access_rules/rules"
                bearer(bw)
                assert bw["body"] == {"mode": "block", "configuration": {"target": "ip", "value": "203.0.113.69"},
                                      "notes": f"Blocked from case #{case_id}: Credential stuffing from 203.0.113.69"}, bw["body"]
                assert top["done"].output["would_do"]["content"] == (
                    "Cloudflare account IP Access rule (block, all zones) for 203.0.113.69: HTTP 200 "
                    "(created, rule 92f17202ed8bd63d69a66b86a49a8f6b).")
                assert top["no_ip"].status == "skipped"
                top, _ = await dry(wf, case_id=case_id, mocks={"block": ok(
                    {"success": False, "errors": [{"code": 10009, "message": "firewallaccessrules.api.duplicate_of_existing"}]}, 400)})
                assert "HTTP 400 (not created" in top["done"].output["would_do"]["content"]
                top, _ = await dry(wf, case_id=bare_id)
                assert top["block"].status == "skipped" and top["done"].status == "skipped"
                assert "no IP artifact" in top["no_ip"].output["would_do"]["content"]
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))  # runs and steps cascade
            await db.execute(delete(CaseArtifact).where(CaseArtifact.id.in_(ids["ca"])))
            await db.execute(delete(Artifact).where(Artifact.id.in_(ids["art"])))
            await db.execute(delete(Case).where(Case.id.in_(ids["case"])))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))  # throwaway tenant's own rows
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_cloudflare_templates_dry_run(monkeypatch):
    asyncio.run(_scenario(_queue(monkeypatch)))
