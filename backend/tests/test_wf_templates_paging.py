"""PagerDuty / Opsgenie escalation templates: load + validate, and dry-run against a sample escalation payload."""
import asyncio
import secrets

import httpx
from sqlalchemy import delete

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseSeverity
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow
from app.utils.crypto import encrypt
from app.workflows import templates as tpl
from app.workflows.validation import secret_names_in_graph, workflow_errors
from tests.test_wf_templates_identity import _drain, _steps

KEYS = {
    "pagerduty-page-on-escalation": "case.escalated",
    "pagerduty-resolve-on-escalation-closed": "case.escalation_closed",
    "opsgenie-alert-on-escalation": "case.escalated",
    "opsgenie-close-on-escalation-closed": "case.escalation_closed",
}
OG_HOST = "api.eu.opsgenie.com"


def _payload(case_id, event, group="Senior SOC"):
    esc = {"id": 7, "dedup_key": f"sochub-case-{case_id}-esc-7", "status": "pending",
           "tier": {"position": 1, "name": "Tier 2"}, "group": {"id": 3, "name": group},
           "reason": "Possible ransomware", "summary": "Host encrypting shares", "requested_help": "Contain the host",
           "case_url": f"https://soc.example.com/cases/{case_id}"}
    return {"event": event, "case_id": case_id, "alert_id": None, "changes": {"escalation": esc}}


def test_paging_templates_load_and_validate():
    tpl.reset_cache()
    for key, trig in KEYS.items():
        t = tpl.get_template(key)
        assert t["trigger_type"] == trig and t["kind"] == "triage"  # paging, not containment: exempt from the guard/task invariants of kind=response and t["trigger_filter"] is None
        assert t["vendor"] in ("PagerDuty", "Opsgenie")
        assert "Escalation paging" in t["setup"]
        params = {p["key"]: ("SOC" if p["key"] == "OPSGENIE_TEAM" else OG_HOST) for p in t["params"]}
        out = tpl.apply_params(t, params)
        assert workflow_errors(out["graph"], t["trigger_type"], out["trigger_filter"], None) == [], key
        assert secret_names_in_graph(out["graph"]) <= {s["name"] for s in t["secrets"]}, key
        if t["vendor"] == "Opsgenie":
            assert out["secrets"][0]["hosts"] == [OG_HOST]
        else:
            assert t["secrets"][0]["hosts"] == ["events.pagerduty.com"]


async def _scenario(queue):
    h = secrets.token_hex(3)
    ids = {"wf": [], "case": None, "sec": []}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-tpl-pg", slug=f"wf-tpl-pg-{h}")
        u = User(email=f"wf-tpl-pg-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u])
        await db.flush()
        tid, uid = t.id, u.id
        try:
            for name, hosts in (("PAGERDUTY_ROUTING_KEY", ["events.pagerduty.com"]), ("OPSGENIE_API_KEY", [OG_HOST])):
                s = TenantSecret(tenant_id=tid, name=name, value_enc=encrypt("x-" + secrets.token_hex(4)), allowed_hosts=hosts)
                db.add(s)
                await db.flush()
                ids["sec"].append(s.id)
            case = Case(title="Ransomware on FS01", tenant_id=tid, severity=CaseSeverity.HIGH)
            db.add(case)
            await db.flush()
            ids["case"] = case.id
            await db.commit()
            cid = case.id
            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.require_admin] = lambda: u
            tpl.reset_cache()
            dedup = f"sochub-case-{cid}-esc-7"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                async def run(key, params, event, group="Senior SOC"):
                    r = await c.post(f"/api/v1/workflows/templates/{key}/install", json={"params": params})
                    assert r.status_code == 201, r.text
                    d = r.json()
                    ids["wf"].append(d["workflow"]["id"])
                    assert d["workflow"]["validation_errors"] in (None, []), d["workflow"]
                    assert d["missing_secrets"] == [], key
                    r = await c.post(f"/api/v1/workflows/{d['workflow']['id']}/dry-run", json={
                        "payload": _payload(cid, event, group), "case_id": cid})
                    assert r.status_code == 200, r.text
                    await _drain(queue)
                    steps = await _steps(r.json()["run_id"])
                    return next(iter(v for k, v in steps.items() if k != "start"))[0][1]["would_do"]

                wd = await run("pagerduty-page-on-escalation", {}, "case.escalated")
                assert wd["url"] == "https://events.pagerduty.com/v2/enqueue" and wd["method"] == "POST"
                b = wd["body"]
                assert b["event_action"] == "trigger" and b["dedup_key"] == dedup
                assert b["payload"]["severity"] == "error" and b["payload"]["source"] == "soc-hub"
                assert b["payload"]["summary"] == f"[SOC Hub] Case #{cid} escalated to Senior SOC: Ransomware on FS01"
                assert b["payload"]["custom_details"] == {"reason": "Possible ransomware", "summary": "Host encrypting shares",
                                                          "requested_help": "Contain the host", "tier": "Tier 2"}
                assert b["links"][0]["href"] == f"https://soc.example.com/cases/{cid}"
                assert "PAGERDUTY_ROUTING_KEY" in b["routing_key"]  # masked secret placeholder

                wd = await run("pagerduty-resolve-on-escalation-closed", {}, "case.escalation_closed")
                assert wd["body"]["event_action"] == "resolve" and wd["body"]["dedup_key"] == dedup

                og = {"OPSGENIE_API_HOST": OG_HOST, "OPSGENIE_TEAM": "SOC"}
                LONG = "Very long on-call group name " * 6
                wd = await run("opsgenie-alert-on-escalation", og, "case.escalated", LONG)
                assert wd["url"] == f"https://{OG_HOST}/v2/alerts"
                b = wd["body"]
                assert b["message"] == f"[SOC Hub] Case #{cid} escalated to {LONG}: Ransomware on FS01"[:127] + "..." and len(b["message"]) == 130
                assert b["alias"] == dedup and b["priority"] == "P2"
                assert b["responders"] == [{"name": "SOC", "type": "team"}]
                assert b["description"] == "Possible ransomware\n\nHost encrypting shares"
                assert "GenieKey" in wd["headers"]["Authorization"]

                wd = await run("opsgenie-close-on-escalation-closed", {"OPSGENIE_API_HOST": OG_HOST}, "case.escalation_closed")
                assert wd["url"] == f"https://{OG_HOST}/v2/alerts/{dedup}/close?identifierType=alias"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(Workflow).where(Workflow.id.in_(ids["wf"])))  # runs/steps cascade
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            if ids["case"]:
                await db.execute(delete(Case).where(Case.id == ids["case"]))
            await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["sec"])))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()


def test_paging_templates_dry_run(monkeypatch):
    import app.tasks.workflows as tw
    queue = []
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: queue.append(("advance", rid)))
    monkeypatch.setattr(tw.execute_step_task, "delay", lambda sid: queue.append(("step", sid)))
    monkeypatch.setattr(tw.loop_tick_task, "delay", lambda sid: queue.append(("tick", sid)))
    asyncio.run(_scenario(queue))
