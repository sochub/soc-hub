#!/usr/bin/env python3
"""Build the API fixtures for the static website demo (frontend `npm run build:demo`).

Creates a fictional tenant ("Greenbow Shrimp Co.", Forrest Gump themed) with made-up analysts, seeds synthetic incidents, plays a
demo story through the real API, then records the JSON of every GET the UI needs.

RUN ONLY AGAINST A THROWAWAY DATABASE: scripts/build-demo.sh starts one and tears it down. The
script refuses to run if the demo tenant already exists.

    python -m app.scripts.demo_fixtures > fixtures.json
"""
import asyncio
import contextlib
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

import app.db.base  # noqa: F401 — register models
from app.auth import sessions
from app.core.security import get_password_hash, issue_access_token
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.case import Alert, Case, CaseLink, TimelineEvent
from app.models.case_task import CaseTask
from app.models.containment import ContainmentAction
from app.models.membership import TenantMembership
from app.models.playbook import PlaybookTemplate
from app.models.suppression_rule import SuppressionRule
from app.models.tenant import Tenant
from app.models.ticket_connection import TicketConnection
from app.models.user import User
from app.models.workflow import Workflow
from app.scripts import seed_incidents, seed_playbooks

# Fictional company and people, themed on Forrest Gump (no real company: "Bubba Gump" is a real chain).
SLUG = "greenbow-demo"
ORG = "Greenbow Shrimp Co."
PEOPLE = [("Forrest Gump", "forrest@greenbow.example", "admin"),
          ("Jenny Curran", "jenny@greenbow.example", "analyst"),
          ("Lt. Dan Taylor", "lt.dan@greenbow.example", "analyst"),
          ("Bubba Blue", "bubba@greenbow.example", "analyst")]
CASES = [
    ("Phishing: \"Your shrimp boat insurance has expired\"", "Three captains received the same lure; one clicked from the dock."),
    ("Impossible travel: Forrest signed in from Alabama, Maine and Santa Monica in one day",
     "Identity alert. Turns out he really is running across the country, but the Santa Monica login came from a VPN exit."),
    ("Ransomware note on SHRIMP-BOAT-01 navigation laptop", "EDR blocked encryption after 14 files; the boat is still at sea."),
    ("Credential stuffing against the Greenbow payroll portal", "Thousands of failed logins from a residential proxy network."),
    ("chocolate-box.exe flagged by EDR on the front desk PC", "Nobody knows what's inside. That's exactly the problem."),
    ("ICMP tunnel from PING-PONG-01 to an external host", "Ping-pong traffic all night long. Too regular to be a game."),
    ("Lt. Dan's account added to Domain Admins without a change ticket", "Privilege escalation alert from the directory audit log."),
    ("Bus-stop bench kiosk beaconing every 60 seconds", "The lobby kiosk talks to a host nobody recognizes."),
    ("Exfiltration attempt: shrimp recipe database (all of them)", "Large export of the recipes table to cloud storage."),
    ("Hurricane season backup check failed on SRV-ORDERS-01", "Nightly restore test failed; the boats only survive storms with backups."),
    ("Mailbox forwarding rule on Jenny's account to an external address", "Rule created from an unfamiliar device. Jenny says it wasn't her."),
    ("Rogue Wi-Fi \"Greenbow-Free-WiFi\" at the Savannah office", "Evil-twin access point spotted near the waiting area."),
    ("Fake \"fruit company\" investment email to finance", "Wire-transfer request impersonating our investment advisor."),
    ("DDoS against the shrimp-orders API", "Order page timing out during the lunch rush."),
    ("Bubba's laptop: new persistence via scheduled task", "Scheduled task launches an unsigned updater every boot."),
]
COMMENTS = [
    "Picked this up. Running through the logs end to end, and then I'll just keep running. 🏃",
    "Lt. Dan thinks it's a false positive. Checking anyway, he said that about the hurricane too.",
    "Bubba listed every shrimp-related domain the user visited. It's a **long** list:\n- shrimp-cocktail.example\n- shrimp-gumbo.example\n- shrimp-burger.example",
    "Jenny confirmed she didn't create that rule. Rotating her credentials now.",
    "Asset owner is out on the shrimp boat with no signal. Waiting for confirmation.",
    "Mama always said to check the hash before trusting a file. Hash is unknown to every source. 🍫",
    "Blocked at the proxy. Watching for the beacon to stop:\n```\nGET https://update-portal.example/c2 200 (every 60s)\n```",
    "Contained. Writing up lessons learned while it's fresh.",
]


def themed_case(i: int) -> tuple[str, str]:
    title, desc = CASES[i % len(CASES)]
    return (title if i < len(CASES) else f"{title} (again)"), desc
STORY_EVENTS = [  # (hours after case creation, ATT&CK category, title)
    (0, "initial_access", "Phishing link opened on the dock from a fake insurance email"),
    (1, "execution", "PowerShell download cradle spawned by Office"),
    (2, "persistence", "Scheduled task created for updater.exe"),
    (3, "credential_access", "LSASS memory read by unsigned binary"),
    (5, "command_and_control", "HTTPS beacon to update-portal.example every 60s"),
    (7, "remediation", "Host isolated and credentials rotated"),
]


async def setup() -> tuple[int, str]:
    async with AsyncSessionLocal() as db:
        if (await db.execute(select(Tenant).where(Tenant.slug == SLUG))).scalars().first():
            sys.exit(f"Tenant {SLUG} already exists: run this against a throwaway database only.")
        t = Tenant(name=ORG, slug=SLUG)
        db.add(t)
        await db.flush()
        for name, email, role in PEOPLE:
            u = User(email=email, full_name=name, is_active=True,
                     hashed_password=get_password_hash(secrets.token_urlsafe(24)))
            db.add(u)
            await db.flush()
            db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role=role))
        await db.commit()
        tid = t.id
    await seed_playbooks.seed()
    async with AsyncSessionLocal() as db:
        await seed_incidents.seed(db, tid, 30, 30, alerts=8)
        # Two more clients for the MSSP portfolio: Forrest works in both.
        forrest = (await db.execute(select(User).where(User.email == PEOPLE[0][1]))).scalars().one()
        for name, slug, role, n in (("Jenny's Bait & Tackle", "jennys-bait-demo", "admin", 9),
                                    ("Lt. Dan's Marine Supply", "ltdan-marine-demo", "analyst", 6)):
            other = Tenant(name=name, slug=slug)
            db.add(other)
            await db.flush()
            db.add(TenantMembership(user_id=forrest.id, tenant_id=other.id, role=role))
            await db.commit()
            await seed_incidents.seed(db, other.id, n, 20, alerts=3)
        # Re-dress the generic seed in the Greenbow story: titles, descriptions, comments, alerts.
        for i, case in enumerate((await db.execute(select(Case).where(Case.tenant_id == tid).order_by(Case.id))).scalars()):
            case.title, case.description = themed_case(i)
        comments = (await db.execute(select(TimelineEvent).join(Case, Case.id == TimelineEvent.case_id).where(
            Case.tenant_id == tid, TimelineEvent.event_type == "comment").order_by(TimelineEvent.id))).scalars()
        for i, ev in enumerate(comments):
            ev.content = COMMENTS[i % len(COMMENTS)]
        for i, alert in enumerate((await db.execute(select(Alert).where(Alert.tenant_id == tid).order_by(Alert.id))).scalars()):
            alert.title = CASES[(i * 2 + 1) % len(CASES)][0]
        await db.commit()
        # The API only accepts tokens of a live server-side session.
        owner = (await db.execute(select(User).where(User.email == PEOPLE[0][1]))).scalars().one()
        row, _refresh = await sessions.create_session(db, owner, kind="web", amr="pwd", tenant_id=tid)
        await db.commit()
    return tid, issue_access_token(owner, tid, sid=row.id)


async def story(c: httpx.AsyncClient, tid: int) -> list[int]:
    """Enrich three cases through the API so tasks, ATT&CK, assets, report and activity are populated."""
    async with AsyncSessionLocal() as db:
        cases = (await db.execute(select(Case).where(Case.tenant_id == tid).order_by(Case.created_at.desc()))).scalars().all()
        pb = (await db.execute(select(PlaybookTemplate.id).where(PlaybookTemplate.tenant_id.is_(None),
                                                                 PlaybookTemplate.name.like("Phishing%")))).scalars().first()
    def ok(r: httpx.Response):
        r.raise_for_status()
        return r.json()

    await c.put("/api/v1/tenants/current/report-branding", json={"org_name": f"{ORG} Security", "accent": "#0f766e"})
    imported = ok(await c.post("/api/v1/playbooks/import", json={"template_ids": [pb]}))
    local_pb = (imported.get("imported_ids") or [None])[0]
    # One template per vendor, with placeholder parameters (installed disabled, as in real installs).
    catalog, seen = ok(await c.get("/api/v1/workflows/templates")), set()
    for t in catalog:
        if t.get("vendor") in seen or len(seen) >= 8:
            continue
        seen.add(t.get("vendor"))
        params = {p["key"]: p.get("default") or p.get("placeholder") or "example" for p in t.get("params") or []}
        r = await c.post(f"/api/v1/workflows/templates/{t['key']}/install", json={"params": params})
        if r.status_code >= 300:
            print(f"template {t['key']}: {r.status_code} {r.text[:200]}")  # stdout is stderr here
    # Two playbook tasks run a workflow from the case (enabled, so Run shows): blocking the lure's host at Cloudflare and
    # revoking the clicker's Entra sessions.
    links = {}
    for key, params, words in (("cloudflare-block-ip", {"ACCOUNT_ID": "0123456789abcdef0123456789abcdef"}, "block malicious"),
                               ("entra-revoke-sessions", {}, "reset credentials")):
        r = await c.post(f"/api/v1/workflows/templates/{key}/install", json={"params": params})
        if r.status_code < 300:
            links[words] = r.json()["workflow"]["id"]
        else:  # installed earlier in the story (one template per vendor): reuse it
            name = next(t["name"] for t in catalog if t["key"] == key)
            links[words] = next((w["id"] for w in ok(await c.get("/api/v1/workflows/")) if w["name"] == name), None)
    links = {k: v for k, v in links.items() if v}
    async with AsyncSessionLocal() as db:
        for wf in (await db.execute(select(Workflow).where(Workflow.id.in_(list(links.values()) or [-1])))).scalars():
            wf.enabled = True
        await db.commit()
    if local_pb and links:
        pb_full = ok(await c.get(f"/api/v1/playbooks/{local_pb}"))
        tasks_in = [{"phase": t["phase"], "title": t["title"], "description": t.get("description"), "order": t["order"],
                     "workflow_id": next((wid for w, wid in links.items() if t["title"].lower().startswith(w)), None)}
                    for t in pb_full["tasks"]]
        ok(await c.put(f"/api/v1/playbooks/{local_pb}", json={"tasks": tasks_in}))
    hero = [x for x in cases if x.status.value in ("closed", "resolved")][:2] + [x for x in cases if x.status.value == "in_progress"][:1]
    hero_titles = ["Phishing: \"Your shrimp boat insurance has expired\"", "Phishing: fake Shrimp Festival ticket giveaway",
                   "Phishing: \"Hurricane relief\" donation link sent to all captains"]
    for case, title in zip(hero, hero_titles):
        base = f"/api/v1/cases/{case.id}"
        await c.put(base, json={"title": title, "description": "Captain clicked a lure on the dock; the payload "
                                 "beaconed out until SHRIMP-BOAT-01 was isolated."})
        if local_pb:
            tasks = ok(await c.post(f"{base}/apply-playbook/{local_pb}"))
            done = len(tasks) if case.status.value in ("closed", "resolved") else len(tasks) // 2
            for t in tasks[:done]:
                await c.put(f"{base}/tasks/{t['id']}", json={"status": "done"})
        start = case.created_at
        for h, cat, title in STORY_EVENTS:
            await c.post(f"{base}/events", json={"event_date": (start + timedelta(hours=h)).isoformat(), "title": title,
                                                 "category": cat, "source": "EDR", "flagged": cat == "initial_access"})
        for name, ip, status in (("SHRIMP-BOAT-01", "192.0.2.45", "compromised"), ("SRV-ORDERS-01", "198.51.100.23", "not_compromised")):
            await c.post(f"{base}/assets", json={"name": name, "ip": ip, "compromise_status": status})
        await c.post(f"{base}/timeline", json={"event_type": "comment", "content":
                     "Beacon confirmed in proxy logs:\n```\nGET https://update-portal.example/c2 200 (every 60s)\n```\nIsolating **SHRIMP-BOAT-01** now. Forrest, please stop running long enough to reboot it."})
        await c.put(f"{base}/report", json={
            "executive_summary": "A shrimp-boat captain opened a fake insurance renewal email. The payload set up a beacon "
                                 "to an external host; SHRIMP-BOAT-01 was isolated and credentials rotated the same day.",
            "impact": "One navigation laptop compromised. The shrimp recipe database was not accessed.",
            "lessons_learned": "Block newly registered domains at the proxy; add boat-insurance lures to awareness training.",
            "tlp": "amber"})
    await new_features(c, tid, [x.id for x in hero])
    return [x.id for x in cases]


async def new_features(c: httpx.AsyncClient, tid: int, hero_ids: list[int]) -> None:
    """Data for suppression, ticketing, containment, task results and the portfolio. Vendor calls can't run here,
    so the rows they would produce (ticket links, containment, task results) are written directly."""
    now = datetime.now(timezone.utc)
    await c.put("/api/v1/containment/policy", json={"enabled": True, "daily_cap": 25, "max_targets_per_case": 5, "protected": [
        {"kind": "user", "pattern": "forrest@greenbow.example", "note": "CEO"}, {"kind": "host", "pattern": "dc-*", "note": "domain controllers"},
        {"kind": "ip", "pattern": "10.20.0.0/16", "note": "boat navigation network"}]})
    await c.put("/api/v1/ticketing/connection", json={"provider": "jira", "base_url": "https://greenbow.atlassian.net",
                                                      "credentials": {"email": "soc@greenbow.example", "token": "demo"},
                                                      "settings": {"project_key": "SEC"}, "auto_create_min_severity": "high"})
    rules = [("Okta: Forrest's cross-country runs", [{"field": "source", "op": "equals", "value": "okta"},
                                                      {"field": "title", "op": "contains", "value": "impossible travel"}],
              "He really is running across the country.", 14, 30),
             ("Vulnerability scanner on the dock network", [{"field": "payload.src_ip", "op": "in_cidr", "value": "192.0.2.0/28"}],
              "Approved weekly scan from the boat-yard scanner.", 41, None)]
    async with AsyncSessionLocal() as db:
        for name, conds, reason, hits, days in rules:
            db.add(SuppressionRule(tenant_id=tid, name=name, conditions=conds, reason=reason, hit_count=hits,
                                   last_hit_at=now - timedelta(hours=3), expires_at=now + timedelta(days=days) if days else None))
        await db.flush()
        first_rule = (await db.execute(select(SuppressionRule.id).where(SuppressionRule.tenant_id == tid)
                                       .order_by(SuppressionRule.id))).scalars().first()
        for a in (await db.execute(select(Alert).where(Alert.tenant_id == tid, Alert.status == "pending")
                                   .order_by(Alert.id).limit(2))).scalars():
            a.status, a.suppression_rule_id = "dismissed", first_rule
            a.dismiss_reason = f"Suppressed by rule #{first_rule}: {rules[0][0]}"
        conn = (await db.execute(select(TicketConnection).where(TicketConnection.tenant_id == tid))).scalars().one()
        n = 41
        for cid in hero_ids:  # tickets belong to tasks: the password reset goes to IT in Jira
            for t in (await db.execute(select(CaseTask).where(CaseTask.case_id == cid, CaseTask.title.like("Reset credentials%"))
                                       .order_by(CaseTask.id))).scalars():
                done = t.status == "done"
                db.add(CaseLink(case_id=cid, task_id=t.id, tenant_id=tid, connection_id=conn.id, system="jira",
                                external_id=f"SEC-{n}", url=f"https://greenbow.atlassian.net/browse/SEC-{n}",
                                remote_status="Done" if done else "In Progress", remote_closed=done, last_synced_at=now,
                                pushed_case_status=t.status))
                db.add(TimelineEvent(case_id=cid, event_type="ticket_comment", content=(
                    f"**Bubba Blue** in Jira SEC-{n} (task *{t.title}*):\n\nCaptain's password is reset; MFA re-enrolled "
                    "on the boat tablet. Waiting for him to sign in again.")))
                n += 1
        main = hero_ids[-1]
        for kind, target, action, status, reason in (
                ("host", "SHRIMP-BOAT-01", "Contain host (CrowdStrike)", "active", None),
                ("user", "forrest@greenbow.example", "Suspend user (Okta)", "blocked",
                 "forrest@greenbow.example is protected by the containment policy: user forrest@greenbow.example (CEO)"),
                ("ip", "203.0.113.77", "Block IP (Cloudflare)", "released", None)):
            db.add(ContainmentAction(tenant_id=tid, case_id=main, action=action, target_kind=kind, target=target,
                                     status=status, reason=reason, released_at=now if status == "released" else None,
                                     release_hint="Lift containment in the Falcon console." if kind == "host" else None))
        for cid in hero_ids:
            for t in (await db.execute(select(CaseTask).where(CaseTask.case_id == cid, CaseTask.workflow_id.isnot(None))
                                       .order_by(CaseTask.id))).scalars():
                if t.title.lower().startswith("block malicious"):
                    t.status, t.completed_at = "done", now
                    t.result = "Cloudflare account IP Access rule (block, all zones) for 203.0.113.77: HTTP 200."
                    t.result_status, t.result_at = "ok", now
                elif t.status == "done":
                    t.result = "Entra containment for jenny@greenbow.example: sessions revoked (HTTP 204)."
                    t.result_status, t.result_at = "ok", now
        await db.commit()


async def _verify_fixture() -> dict:
    """The public "was this you?" page reads one question; there's no live workflow wait in the demo."""
    return {"state": "open", "organization": ORG, "message": "Impossible travel: sign-in from Lagos, NG (reported by okta). Was this you?",
            "buttons": ["It was me", "It wasn't me"], "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}


async def capture(c: httpx.AsyncClient, case_ids: list[int]) -> dict:
    static = ["/users/me", "/users/", "/users/mentionable", "/cases/", "/cases/tags", "/alerts/", "/iocs/", "/artifacts/",
              "/stats/", "/playbooks/", "/playbooks/marketplace", "/workflows/", "/workflows/templates", "/workflow-runs/",
              "/notifications/", "/notifications/unread-count", "/tenants/", "/tenants/sla-policies", "/artifact-types/",
              "/ai/info", "/enrichment/config", "/audit-logs", "/audit-logs/facets", "/tenants/current/report-branding",
              "/tenants/current/security", "/tenants/current/guests", "/openapi.json",
              "/suppressions/", "/ticketing/connection", "/ticketing/status", "/containment/policy", "/containment/actions",
              "/portfolio/"]
    per_case = ["/cases/{id}", "/artifacts/case/{id}", "/cases/{id}/tasks", "/cases/{id}/events", "/cases/{id}/assets",
                "/cases/{id}/attachments", "/cases/{id}/follow", "/cases/{id}/report", "/audit-logs/case/{id}",
                "/cases/{id}/report/charts/lifecycle.svg", "/cases/{id}/report/charts/timeline.svg", "/cases/{id}/ticket"]
    out = {}
    paths = static + [p.format(id=i) for i in case_ids for p in per_case]
    for wf in (await c.get("/api/v1/workflows/")).json():
        paths.append(f"/workflows/{wf['id']}")
    for pb in (await c.get("/api/v1/playbooks/")).json():
        paths.append(f"/playbooks/{pb['id']}")
    for t in (await c.get("/api/v1/portfolio/")).json()["tenants"]:
        paths.append(f"/portfolio/{t['id']}/digest")
    for path in paths:
        r = await c.get("/api/v1" + path)
        if r.status_code == 200:
            ctype = r.headers.get("content-type", "")
            out[path] = r.json() if "json" in ctype else r.text
    out["/user-verification/demo"] = await _verify_fixture()
    return out


async def main() -> None:
    with contextlib.redirect_stdout(sys.stderr):  # seed helpers print progress; stdout carries only the JSON
        tid, token = await setup()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo",
                                     headers={"Authorization": f"Bearer {token}"}) as c:
            case_ids = await story(c, tid)
            fixtures = await capture(c, case_ids)
    json.dump({"captured_at": datetime.now(timezone.utc).isoformat(), "responses": fixtures}, sys.stdout,
              ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    asyncio.run(main())
