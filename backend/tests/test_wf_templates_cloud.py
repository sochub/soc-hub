"""Cloud and SaaS templates (GuardDuty, Security Hub, Sentinel, Wiz, Proofpoint TAP, GitHub, generic triage):
install through the API, dry-run with vendor-shaped mocks, assert requests, auth shape and alert_create inputs.

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
from app.models.audit_log import AuditLog
from app.models.case import Alert
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.utils.crypto import encrypt
from app.workflows import templates as tpl
from tests.test_wf_templates_edr import _drain, _queue

SINCE = "2026-10-18T10:00:00Z"
SINCE_MS = 1792317600000  # 2026-10-18T10:00:00Z in epoch ms
SCHEDULE = {"event": "schedule", "fired_at": "2026-10-18T10:05:00.123456+00:00", "since": SINCE,
            "case_id": None, "alert_id": None, "changes": {}}
REGION, WIZ_HOST = "eu-west-1", "api.us17.app.wiz.io"
PARAMS = {"AWS_REGION": REGION, "DETECTOR_ID": "12abc34d567e8fa901bc2d34e56789f0",
          "AZURE_TENANT_ID": "72f988bf-86f1-41af-91ab-2d7cd011db47", "AZURE_SUBSCRIPTION_ID": "d0cfe6b2-9ac0-4464-9919-dccaee2e48c0",
          "RESOURCE_GROUP": "rg-security", "WORKSPACE_NAME": "law-sentinel", "WIZ_API_HOST": WIZ_HOST, "GITHUB_ORG": "acme-corp"}


def ok(body, status=200):
    return {"status": status, "headers": {}, "body": body}


# GuardDuty ListFindings / GetFindings (API reference shapes)
GD_LIST = ok({"findingIds": ["a2c5f1e0b9d84f6d8e1c3b7a9f0e2d41", "b7e9d3c2a1f04e5b9c8d7e6f5a4b3c21"]})
GD_GET = ok({"findings": [
    {"accountId": "111122223333", "arn": "arn:aws:guardduty:eu-west-1:111122223333:detector/12abc/finding/a2c5", "confidence": 8,
     "createdAt": "2026-10-18T09:58:01.512Z", "id": "a2c5f1e0b9d84f6d8e1c3b7a9f0e2d41", "partition": "aws", "region": "eu-west-1",
     "resource": {"resourceType": "Instance", "instanceDetails": {"instanceId": "i-0abcd1234ef567890"}},
     "schemaVersion": "2.0", "service": {"archived": False, "count": 3, "detectorId": "12abc", "serviceName": "guardduty"},
     "severity": 8, "title": "EC2 instance i-0abcd1234ef567890 is communicating with a known C&C server.",
     "type": "Backdoor:EC2/C&CActivity.B", "updatedAt": "2026-10-18T10:01:12.000Z"},
    {"accountId": "111122223333", "id": "b7e9d3c2a1f04e5b9c8d7e6f5a4b3c21", "region": "eu-west-1", "severity": 2,
     "title": "Unprotected port on EC2 instance i-0fff is being probed.", "type": "Recon:EC2/PortProbeUnprotectedPort",
     "createdAt": "2026-10-17T22:00:00.000Z", "updatedAt": "2026-10-18T10:02:00.000Z", "service": {"archived": False, "count": 41}},
]})
# Security Hub GetFindings (ASFF)
SH_FINDINGS = ok({"Findings": [
    {"SchemaVersion": "2018-10-08",
     "Id": "arn:aws:securityhub:eu-west-1:111122223333:security-control/IAM.6/finding/ab6d6a26-a156-48f0-9403-115983e5a956",
     "ProductArn": "arn:aws:securityhub:eu-west-1::product/aws/securityhub", "ProductName": "Security Hub",
     "GeneratorId": "security-control/IAM.6", "AwsAccountId": "111122223333",
     "Title": "Hardware MFA should be enabled for the root user", "Severity": {"Label": "CRITICAL", "Normalized": 90},
     "CreatedAt": "2026-10-01T08:00:00.000Z", "UpdatedAt": "2026-10-18T10:03:00.000Z",
     "Workflow": {"Status": "NEW"}, "RecordState": "ACTIVE",
     "Resources": [{"Type": "AwsAccount", "Id": "AWS::::Account:111122223333"}]},
    {"SchemaVersion": "2018-10-08", "Id": "arn:aws:inspector2:eu-west-1:111122223333:finding/8f1c2d3e4b5a69788a9b0c1d2e3f4a5b",
     "ProductName": "Inspector", "AwsAccountId": "111122223333", "Title": "CVE-2026-1234 - openssl",
     "Severity": {"Label": "MEDIUM", "Normalized": 40}, "UpdatedAt": "2026-10-18T10:04:00.000Z", "RecordState": "ACTIVE"},
]})
# Sentinel Incidents - List (2025-09-01 sample)
INC = "/subscriptions/d0cfe6b2-9ac0-4464-9919-dccaee2e48c0/resourceGroups/rg-security/providers/Microsoft.OperationalInsights/workspaces/law-sentinel/providers/Microsoft.SecurityInsights/incidents/"
SENTINEL = ok({"value": [
    {"id": INC + "73e01a99-5cd7-4139-a149-9f2736ff2ab5", "name": "73e01a99-5cd7-4139-a149-9f2736ff2ab5",
     "type": "Microsoft.SecurityInsights/incidents", "etag": "\"0300bf09-0000-0000-0000-5c37296e0000\"",
     "properties": {"createdTimeUtc": "2026-10-18T10:01:30Z", "lastModifiedTimeUtc": "2026-10-18T10:01:30Z",
                    "title": "Multistage attack involving password spray", "severity": "High", "status": "New",
                    "incidentNumber": 3177, "incidentUrl": "https://portal.azure.com/#asset/x", "labels": [],
                    "additionalData": {"alertsCount": 2, "tactics": ["CredentialAccess"]}}},
    {"id": INC + "a1b2c3d4-0000-4139-a149-9f2736ff0000", "name": "a1b2c3d4-0000-4139-a149-9f2736ff0000",
     "properties": {"createdTimeUtc": "2026-10-18T10:03:00Z", "title": "Rare RDP connection", "severity": "Informational",
                    "status": "New", "incidentNumber": 3178}},
]})
# Wiz issuesV2
WIZ = ok({"data": {"issues": {"nodes": [
    {"id": "c3f1a5e0-1111-4b8e-9c0d-2e3f4a5b6c7d", "type": "TOXIC_COMBINATION", "severity": "CRITICAL", "status": "OPEN",
     "createdAt": "2026-10-18T10:01:00Z", "updatedAt": "2026-10-18T10:01:00Z",
     "sourceRule": {"__typename": "Control", "id": "wc-id-1", "name": "Publicly exposed VM with high-privilege role"},
     "entitySnapshot": {"id": "e1", "type": "VIRTUAL_MACHINE", "name": "web-prod-01", "cloudPlatform": "AWS",
                        "region": "eu-west-1", "subscriptionExternalId": "111122223333"}, "projects": []},
    {"id": "d4e2b6f1-2222-4b8e-9c0d-2e3f4a5b6c7d", "type": "THREAT_DETECTION", "severity": "LOW", "status": "OPEN",
     "createdAt": "2026-10-18T10:02:00Z", "sourceRule": None, "entitySnapshot": {"name": "ci-runner"}},
], "pageInfo": {"hasNextPage": False, "endCursor": None}}}})
# Proofpoint TAP /v2/siem/all JSON
PP = ok({"queryEndTime": "2026-10-18T10:05:00Z",
         "messagesDelivered": [
             {"GUID": "c26dbea0-80d5-463b-b93c-4e8b708219ce", "messageID": "<x@evil.example>", "subject": "Invoice 4471",
              "sender": "billing@evil.example", "recipient": ["ana@acme.example"], "phishScore": 46, "spamScore": 4,
              "threatsInfoMap": [{"classification": "PHISH", "threat": "http://evil.example/login", "threatID": "t1",
                                  "threatStatus": "active", "threatType": "URL", "threatUrl": "https://threatinsight.proofpoint.com/x"}]},
             {"GUID": "d37ecfb1-91e6-574c-c04d-5f9c819320df", "subject": "Cheap pills", "recipient": ["bob@acme.example"],
              "threatsInfoMap": [{"classification": "spam", "threatID": "t2", "threatType": "URL"}]}],
         "messagesBlocked": [], "clicksBlocked": [],
         "clicksPermitted": [{"id": "a5c9f8bb-4444-4c8e-9a0f-1b2c3d4e5f60", "GUID": "c26dbea0-80d5-463b-b93c-4e8b708219ce",
                              "classification": "PHISH", "clickIP": "192.0.2.1", "clickTime": "2026-10-18T10:03:00Z",
                              "recipient": "ana@acme.example", "threatID": "t1", "url": "http://evil.example/login"}]})
# GitHub org alerts
GH_SECRETS = ok([
    {"number": 2, "created_at": "2026-10-18T10:02:00Z", "html_url": "https://github.com/acme-corp/api/security/secret-scanning/2",
     "state": "open", "secret_type": "aws_access_key_id", "secret_type_display_name": "AWS Access Key ID", "validity": "active",
     "repository": {"id": 1, "full_name": "acme-corp/api"}},
    {"number": 7, "created_at": "2026-10-18T10:00:00Z", "state": "open", "secret_type": "slack_incoming_webhook_url",
     "secret_type_display_name": "Slack Incoming Webhook URL", "validity": "unknown", "repository": {"full_name": "acme-corp/bot"}},
    {"number": 1, "created_at": "2026-10-17T08:00:00Z", "state": "open", "secret_type": "github_personal_access_token",
     "validity": "inactive", "repository": {"full_name": "acme-corp/old"}},  # before since: skipped
])
GH_DEPENDABOT = ok([
    {"number": 42, "state": "open", "created_at": "2026-10-18T10:01:00Z", "html_url": "https://github.com/acme-corp/web/security/dependabot/42",
     "dependency": {"package": {"ecosystem": "npm", "name": "lodash"}, "manifest_path": "package-lock.json"},
     "security_advisory": {"ghsa_id": "GHSA-xxxx", "summary": "Prototype pollution in lodash", "severity": "high"},
     "security_vulnerability": {"severity": "high"}, "repository": {"full_name": "acme-corp/web"}},
    {"number": 43, "state": "open", "created_at": "2026-10-18T10:04:00Z",
     "dependency": {"package": {"ecosystem": "pip", "name": "jinja2"}},
     "security_advisory": {"summary": "Sandbox escape in Jinja", "severity": "medium"}, "repository": {"full_name": "acme-corp/api"}},
])


async def _run(run_id):
    """(run, {node_id: step} for top-level steps, [would_do of every create* step in loop children])"""
    async with AsyncSessionLocal() as db:
        run = await db.get(WorkflowRun, run_id)
        top = {s.node_id: s for s in (await db.execute(
            select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))).scalars()}
        kid_ids = (await db.execute(select(WorkflowRun.id).where(WorkflowRun.parent_run_id == run_id))).scalars().all()
        creates = [s.output["would_do"] for s in (await db.execute(select(WorkflowRunStep).where(
            WorkflowRunStep.run_id.in_(kid_ids or [-1]), WorkflowRunStep.node_id.like("create%")))).scalars()]
        return run, top, sorted((c["source"], c["external_id"], c["title"], c["severity"]) for c in creates)


def _no_secret_values(obj):
    assert "val-" not in json.dumps(obj, default=str), obj


async def _scenario(q):
    h = secrets.token_hex(3)
    ids = {"wf": [], "alert": [], "sec": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-cloud", slug=f"wf-cloud-{h}")
        u = User(email=f"wf-cloud-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.commit()
        tid, uid = t.id, u.id
        try:
            aws = [f"guardduty.{REGION}.amazonaws.com", f"securityhub.{REGION}.amazonaws.com"]
            az = ["login.microsoftonline.com", "management.azure.com"]
            wiz = ["auth.app.wiz.io", WIZ_HOST]
            for name, hosts in (("AWS_ACCESS_KEY_ID", aws), ("AWS_SECRET_ACCESS_KEY", aws),
                                ("SENTINEL_CLIENT_ID", az), ("SENTINEL_CLIENT_SECRET", az),
                                ("WIZ_CLIENT_ID", wiz), ("WIZ_CLIENT_SECRET", wiz),
                                ("PROOFPOINT_PRINCIPAL", ["tap-api-v2.proofpoint.com"]),
                                ("PROOFPOINT_SECRET", ["tap-api-v2.proofpoint.com"]), ("GITHUB_TOKEN", ["api.github.com"])):
                s = TenantSecret(tenant_id=tid, name=name, value_enc=encrypt(f"val-{name}-{h}"), allowed_hosts=hosts)
                db.add(s)
                await db.flush()
                ids["sec"].append(s.id)
            high = Alert(tenant_id=tid, source="wiz", external_id="c3f1a5e0", status="pending", severity="high",
                         title="Publicly exposed VM with high-privilege role on web-prod-01", payload=WIZ["body"]["data"]["issues"]["nodes"][0])
            db.add(high)
            await db.flush()
            ids["alert"].append(high.id)
            await db.commit()
            high_id = high.id

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

                async def dry(wf_id, expect="succeeded", **body):
                    r = await c.post(f"/api/v1/workflows/{wf_id}/dry-run", json=body)
                    assert r.status_code == 200, r.text
                    await _drain(q)
                    run, top, creates = await _run(r.json()["run_id"])
                    assert run.status == expect, (run.error, {k: (s.status, s.error) for k, s in top.items()})
                    return top, creates

                async def failed_with(wf_id, node, message, mocks):
                    top, creates = await dry(wf_id, expect="failed", payload=SCHEDULE, mocks=mocks)
                    assert creates == [] and top[node].status == "failed" and top[node].error == message, \
                        {k: (s.status, s.error) for k, s in top.items()}

                # ── GuardDuty (aws_sigv4) ──
                wf = await install("aws-guardduty-findings-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"list": GD_LIST, "details": GD_GET})
                lw = top["list"].output["would_do"]
                assert lw["method"] == "POST" and lw["url"] == (
                    f"https://guardduty.{REGION}.amazonaws.com/detector/12abc34d567e8fa901bc2d34e56789f0/findings")
                assert json.loads(lw["body"]) == {
                    "findingCriteria": {"criterion": {"updatedAt": {"greaterThanOrEqual": SINCE_MS},
                                                      "service.archived": {"equals": ["false"]}}},
                    "sortCriteria": {"attributeName": "updatedAt", "orderBy": "DESC"}, "maxResults": 50}, lw["body"]
                a = lw["auth"]
                assert (a["type"], a["region"], a["service"]) == ("aws_sigv4", REGION, "guardduty")
                assert "⟦secret:AWS_SECRET_ACCESS_KEY#" in a["secret_access_key"] and "⟦secret:AWS_ACCESS_KEY_ID#" in a["access_key_id"]
                gw = top["details"].output["would_do"]
                assert gw["url"].endswith("/findings/get") and gw["body"] == {"findingIds": GD_LIST["body"]["findingIds"]}
                _no_secret_values([lw, gw])
                assert creates == [
                    ("aws-guardduty", "a2c5f1e0b9d84f6d8e1c3b7a9f0e2d41",
                     "EC2 instance i-0abcd1234ef567890 is communicating with a known C&C server. (111122223333/eu-west-1)", "high"),
                    ("aws-guardduty", "b7e9d3c2a1f04e5b9c8d7e6f5a4b3c21",
                     "Unprotected port on EC2 instance i-0fff is being probed. (111122223333/eu-west-1)", "low"),
                ], creates
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"list": ok({"findingIds": []})})
                assert top["details"].status == "skipped" and creates == []
                await failed_with(wf, "list_failed", "GuardDuty ListFindings failed: HTTP 403", {"list": ok(
                    {"__type": "AccessDeniedException", "message": "User is not authorized: val-leak"}, 403)})

                # ── Security Hub (aws_sigv4) ──
                wf = await install("aws-securityhub-findings-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": SH_FINDINGS})
                pw = top["pull"].output["would_do"]
                assert pw["method"] == "POST" and pw["url"] == f"https://securityhub.{REGION}.amazonaws.com/findings"
                f = pw["body"]["Filters"]
                assert f["UpdatedAt"] == [{"Start": SINCE, "End": "2026-10-18T10:05:00Z"}]
                assert f["RecordState"] == [{"Value": "ACTIVE", "Comparison": "EQUALS"}]
                assert [x["Value"] for x in f["WorkflowStatus"]] == ["NEW", "NOTIFIED"]
                assert [x["Value"] for x in f["SeverityLabel"]] == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
                assert pw["body"]["MaxResults"] == 100 and pw["body"]["SortCriteria"] == [{"Field": "UpdatedAt", "SortOrder": "desc"}]
                assert (pw["auth"]["region"], pw["auth"]["service"]) == (REGION, "securityhub")
                _no_secret_values(pw)
                assert creates == [
                    ("aws-securityhub", "arn:aws:inspector2:eu-west-1:111122223333:finding/8f1c2d3e4b5a69788a9b0c1d2e3f4a5b",
                     "CVE-2026-1234 - openssl (Inspector, 111122223333)", "medium"),
                    ("aws-securityhub", SH_FINDINGS["body"]["Findings"][0]["Id"],
                     "Hardware MFA should be enabled for the root user (Security Hub, 111122223333)", "critical"),
                ], creates
                await failed_with(wf, "failed", "Security Hub GetFindings failed: HTTP 400",
                                  {"pull": ok({"Message": "Invalid filter", "Code": "InvalidInputException"}, 400)})

                # ── Sentinel (oauth2) ──
                wf = await install("sentinel-incidents-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": SENTINEL})
                pw = top["pull"].output["would_do"]
                assert pw["url"] == (
                    "https://management.azure.com/subscriptions/d0cfe6b2-9ac0-4464-9919-dccaee2e48c0/resourceGroups/rg-security"
                    "/providers/Microsoft.OperationalInsights/workspaces/law-sentinel/providers/Microsoft.SecurityInsights/incidents"
                    "?api-version=2025-09-01&$filter=properties/createdTimeUtc%20ge%202026-10-18T10%3A00%3A00Z"
                    "&$orderby=properties/createdTimeUtc%20asc&$top=100"), pw["url"]
                a = pw["auth"]
                assert a["type"] == "oauth2_client_credentials" and a["scope"] == "https://management.azure.com/.default"
                assert a["token_url"] == "https://login.microsoftonline.com/72f988bf-86f1-41af-91ab-2d7cd011db47/oauth2/v2.0/token"
                _no_secret_values(pw)
                assert creates == [
                    ("ms-sentinel", "73e01a99-5cd7-4139-a149-9f2736ff2ab5", "#3177 Multistage attack involving password spray", "high"),
                    ("ms-sentinel", "a1b2c3d4-0000-4139-a149-9f2736ff0000", "#3178 Rare RDP connection", "info"),
                ], creates
                await failed_with(wf, "failed", "Sentinel incidents pull failed: HTTP 403", {"pull": ok(
                    {"error": {"code": "AuthorizationFailed", "message": "client val-x does not have authorization"}}, 403)})

                # ── Wiz (oauth2 + audience, GraphQL) ──
                wf = await install("wiz-issues-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": WIZ})
                pw = top["pull"].output["would_do"]
                assert pw["method"] == "POST" and pw["url"] == f"https://{WIZ_HOST}/graphql"
                assert "issuesV2(filterBy: $filterBy" in pw["body"]["query"]
                assert pw["body"]["variables"] == {"first": 100, "filterBy": {"createdAt": {"after": SINCE}, "status": ["OPEN", "IN_PROGRESS"]},
                                                   "orderBy": {"field": "CREATED_AT", "direction": "ASC"}}
                a = pw["auth"]
                assert (a["token_url"], a["audience"]) == ("https://auth.app.wiz.io/oauth/token", "wiz-api")
                _no_secret_values(pw)
                assert creates == [
                    ("wiz", "c3f1a5e0-1111-4b8e-9c0d-2e3f4a5b6c7d", "Publicly exposed VM with high-privilege role on web-prod-01", "critical"),
                    ("wiz", "d4e2b6f1-2222-4b8e-9c0d-2e3f4a5b6c7d", "THREAT_DETECTION on ci-runner", "low"),
                ], creates
                await failed_with(wf, "failed", "Wiz issues pull failed: HTTP 200 (GraphQL errors)", {"pull": ok(
                    {"errors": [{"message": "Unauthorized: val-x"}], "data": None})})
                await failed_with(wf, "failed", "Wiz issues pull failed: HTTP 401", {"pull": ok({"error": "unauthorized"}, 401)})

                # ── Proofpoint TAP (basic) ──
                wf = await install("proofpoint-tap-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"pull": PP})
                pw = top["pull"].output["would_do"]
                # 5 min since the previous run + 5 min overlap
                assert pw["url"] == "https://tap-api-v2.proofpoint.com/v2/siem/all?format=json&sinceSeconds=600", pw["url"]
                a = pw["auth"]
                assert a["type"] == "basic" and "⟦secret:PROOFPOINT_PRINCIPAL#" in a["username"] and "⟦secret:PROOFPOINT_SECRET#" in a["password"]
                _no_secret_values(pw)
                assert creates == [
                    ("proofpoint-tap", "click:a5c9f8bb-4444-4c8e-9a0f-1b2c3d4e5f60",
                     "Permitted click on phish URL by ana@acme.example: http://evil.example/login", "critical"),
                    ("proofpoint-tap", "msg:c26dbea0-80d5-463b-b93c-4e8b708219ce", "Delivered phish email to ana@acme.example: Invoice 4471", "high"),
                    ("proofpoint-tap", "msg:d37ecfb1-91e6-574c-c04d-5f9c819320df", "Delivered spam email to bob@acme.example: Cheap pills", "low"),
                ], creates
                late = {**SCHEDULE, "since": "2026-10-18T07:00:00Z"}  # previous success 3 h ago: capped at one hour
                top, _ = await dry(wf, payload=late, mocks={"pull": PP})
                assert top["pull"].output["would_do"]["url"].endswith("sinceSeconds=3600")
                await failed_with(wf, "failed", "Proofpoint TAP pull failed: HTTP 401", {"pull": ok("Unauthorized", 401)})

                # ── GitHub (bearer header) ──
                wf = await install("github-security-alerts-pull")
                top, creates = await dry(wf, payload=SCHEDULE, mocks={"secrets_pull": GH_SECRETS, "dependabot_pull": GH_DEPENDABOT})
                sw, dw = top["secrets_pull"].output["would_do"], top["dependabot_pull"].output["would_do"]
                assert sw["url"] == "https://api.github.com/orgs/acme-corp/secret-scanning/alerts?state=open&sort=created&direction=desc&per_page=100"
                assert dw["url"] == "https://api.github.com/orgs/acme-corp/dependabot/alerts?state=open&sort=created&direction=desc&per_page=100"
                for w in (sw, dw):
                    assert w["headers"]["Accept"] == "application/vnd.github+json" and w["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
                    assert w["headers"]["Authorization"].startswith("Bearer ⟦secret:GITHUB_TOKEN#")
                _no_secret_values([sw, dw])
                assert creates == [
                    ("github", "dependabot:acme-corp/api:43", "Sandbox escape in Jinja (jinja2, acme-corp/api)", "medium"),
                    ("github", "dependabot:acme-corp/web:42", "Prototype pollution in lodash (lodash, acme-corp/web)", "high"),
                    ("github", "secret-scanning:acme-corp/api:2", "AWS Access Key ID leaked in acme-corp/api", "critical"),
                    ("github", "secret-scanning:acme-corp/bot:7", "Slack Incoming Webhook URL leaked in acme-corp/bot", "high"),
                ], creates
                top, creates = await dry(wf, expect="failed", payload=SCHEDULE, mocks={
                    "secrets_pull": GH_SECRETS, "dependabot_pull": ok({"message": "Bad credentials"}, 401)})
                assert top["dependabot_failed"].error == "GitHub Dependabot pull failed: HTTP 401"

                # ── generic triage ──
                wf = await install("generic-alerts-to-case")
                top, _ = await dry(wf, alert_id=high_id)
                p = top["promote"].output["would_do"]
                assert p["mode"] == "group" and p["window_hours"] == 24 and p["severity"] == "high" and p["tags"] == "wiz"
                assert p["group_key"] == "source:wiz:Publicly exposed VM with high-privilege role on web-prod-01", p
                async with AsyncSessionLocal() as db2:
                    wf_row = await db2.get(Workflow, wf)
                    assert wf_row.trigger_filter == "alert.severity in ['critical', 'high']" and wf_row.enabled is False
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))  # runs and steps cascade
            await db.execute(delete(Alert).where(Alert.id.in_(ids["alert"])))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))  # throwaway tenant's own rows
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_cloud_templates_dry_run(monkeypatch):
    asyncio.run(_scenario(_queue(monkeypatch)))
