import asyncio
import hashlib
import hmac
import json
import secrets
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.case import Case, CaseSeverity, CaseStatus, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.notification import CaseFollower, Notification
from app.models.membership import TenantMembership
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.user import User
from app.services import slack_blocks as ui
from app.services import slack_bot
from app.utils.crypto import encrypt

SECRET = "bot-signing-secret"


def _sign(body: bytes, ts: str, secret=SECRET) -> str:
    return "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()


def test_should_alert_threshold_and_crossing():
    assert slack_bot.should_alert("high", "critical") and slack_bot.should_alert("high", "high")
    assert not slack_bot.should_alert("high", "medium") and not slack_bot.should_alert(None, "critical")
    assert slack_bot.should_alert("high", "critical", previous="medium")     # crossed the threshold
    assert not slack_bot.should_alert("high", "critical", previous="high")   # already above: no re-alert


def test_channel_name_is_slack_safe():
    from datetime import datetime
    from app.services.war_room_naming import render_channel_name as r
    d = datetime(2026, 1, 1)
    assert r(None, case_id=42, title="Phishing: \"Your shrimp boat insurance\" EXPIRED!!", created=d) == "inc-0042-phishing-your-shrimp-boat-insurance-expired"
    assert r(None, case_id=7, title="¡¡!!", created=d) == "inc-0007"


def test_card_escapes_user_text():
    c = SimpleNamespace(id=1, tenant_id=1, title="<!channel> hi", description="<@U1>", severity=CaseSeverity.HIGH,
                        status=CaseStatus.OPEN, slack_channel_id=None)
    text = json.dumps(ui.case_card(c, "<b>owner</b>"))
    assert "<!channel>" not in text and "<@U1>" not in text and "&lt;!channel&gt;" in text
    c.title = "<!channel> <https://evil.example|click> <@U1>"
    fallback = ui.card_text(c)
    assert "<!channel>" not in fallback and "<https://" not in fallback and "<@U1>" not in fallback


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    team = f"TBOT{h}"
    calls, homes, events = [], [], []

    async def fake_call(token, method, **args):
        calls.append((method, args))
        if method == "chat.postMessage":
            return {"ok": True, "channel": args.get("channel"), "ts": "1700000000.000100"}
        return {"ok": True}

    async def fake_email(token, slack_user_id):
        return {"UANALYST": f"analyst-{h}@example.com"}.get(slack_user_id)

    import app.tasks.slack as ts
    import app.tasks.triage as tt
    import app.workflows.events as ev
    monkeypatch.setattr(slack_bot, "slack_call", fake_call)
    monkeypatch.setattr(slack_bot, "_slack_email", fake_email)
    monkeypatch.setattr(ts.slack_home_task, "delay", lambda *a: homes.append(a))
    monkeypatch.setattr(tt.run_case_triage_task, "delay", lambda *a: None)
    monkeypatch.setattr(ev, "emit_event", lambda *a, **k: events.append((a, k)))

    async with AsyncSessionLocal() as db:
        t, other = Tenant(name="bot", slug=f"bot-{h}"), Tenant(name="other", slug=f"bot-other-{h}")
        db.add_all([t, other])
        await db.flush()
        u = User(email=f"analyst-{h}@example.com", full_name="Jenny Curran", is_active=True,
                 hashed_password=get_password_hash(secrets.token_urlsafe(16)))
        db.add(u)
        await db.flush()
        mate = User(email=f"mate-{h}@example.com", full_name="Bubba Blue", is_active=True,
                    hashed_password=get_password_hash(secrets.token_urlsafe(16)))
        db.add(mate)
        await db.flush()
        db.add_all([TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"),
                    TenantMembership(user_id=mate.id, tenant_id=t.id, role="analyst"),
                    TenantMembership(user_id=mate.id, tenant_id=other.id, role="viewer")])
        db.add(SlackIntegration(tenant_id=t.id, team_id=team, bot_token_enc=encrypt("xoxb-x"),
                                signing_secret_enc=encrypt(SECRET), alert_channel="C_ALERTS", alert_min_severity="high"))
        mine = Case(title="Beacon to update-portal.example", description="d", tenant_id=t.id, severity=CaseSeverity.HIGH)
        foreign = Case(title="other tenant", description="d", tenant_id=other.id, severity=CaseSeverity.LOW)
        db.add_all([mine, foreign])
        await db.commit()
        ids = SimpleNamespace(t=t.id, other=other.id, user=u.id, mate=mate.id, case=mine.id, foreign=foreign.id)

    now = str(int(time.time()))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            async def post(path, body: bytes, ctype, sig=None):
                return await c.post(path, content=body, headers={
                    "X-Slack-Request-Timestamp": now, "X-Slack-Signature": sig or _sign(body, now), "Content-Type": ctype})

            async def cmd(text, user="UANALYST", **extra):
                body = urlencode({"team_id": team, "user_id": user, "command": "/sochub", "text": text,
                                  "trigger_id": "trig", **extra}).encode()
                return await post("/api/v1/slack/commands", body, "application/x-www-form-urlencoded")

            # --- slash command ---
            assert "sochub commands" in (await cmd("help")).json()["text"]
            assert "isn't linked" in (await cmd(f"case {ids.case}", user="USTRANGER")).json()["text"]
            r = (await cmd(f"case {ids.case}")).json()
            assert r["response_type"] == "ephemeral" and "Beacon to update-portal" in json.dumps(r["blocks"])
            assert "not found" in (await cmd(f"case {ids.foreign}")).json()["text"]  # other tenant's case
            calls.clear()
            assert (await cmd("new Phish on the dock")).status_code == 200
            assert calls[0][0] == "views.open" and calls[0][1]["view"]["callback_id"] == ui.CREATE_CASE
            body = urlencode({"team_id": team, "user_id": "UANALYST", "text": "help"}).encode()
            assert (await post("/api/v1/slack/commands", body, "application/x-www-form-urlencoded",
                               sig=_sign(body, now, "wrong"))).status_code == 401

            # --- create-case modal submission ---
            def submission(meta, title="Phish from Slack", user="UANALYST"):
                return {"type": "view_submission", "team": {"id": team}, "user": {"id": user}, "view": {
                    "callback_id": ui.CREATE_CASE, "private_metadata": json.dumps(meta), "state": {"values": {
                        "title": {"v": {"value": title}},
                        "severity": {"v": {"selected_option": {"value": "critical"}}},
                        "description": {"v": {"value": "clicked on the dock"}},
                        "assign": {"v": {"selected_options": [{"value": "me"}]}}}}}}

            async def submit(p):
                body = urlencode({"payload": json.dumps(p)}).encode()
                return await post("/api/v1/slack/interactions", body, "application/x-www-form-urlencoded")

            r = await submit(submission({"t": ids.t, "src": "https://slack.example/p1"}))
            assert r.json()["response_action"] == "update", r.text
            assert (await submit(submission({"t": ids.other}, title="cross-tenant"))).json()["response_action"] == "errors"
            assert (await submit(submission({"t": ids.t}, title="unlinked", user="USTRANGER"))).json()["response_action"] == "errors"
            async with AsyncSessionLocal() as db:
                made = (await db.execute(select(Case).where(Case.tenant_id.in_([ids.t, ids.other]), Case.title.in_(["Phish from Slack", "cross-tenant", "unlinked"])))).scalars().all()
            assert [(x.title, x.tenant_id, x.owner_id, x.severity) for x in made] == [
                ("Phish from Slack", ids.t, ids.user, CaseSeverity.CRITICAL)]
            assert "https://slack.example/p1" in made[0].description
            assert any(a[1] == "case.created" for a, _ in events)

            # --- Assign…: picker lists the tenant's analysts, submission hands the case over ---
            calls.clear()
            click = {"type": "block_actions", "team": {"id": team}, "user": {"id": "UANALYST"}, "trigger_id": "trig",
                     "actions": [{"action_id": "case_assignto", "value": f"case:{ids.t}:{ids.case}:assignto"}]}
            await submit(click)
            view = calls[0][1]["view"]
            assert view["callback_id"] == ui.ASSIGN_CASE
            assert [o["text"]["text"] for o in view["blocks"][0]["element"]["options"]] == ["Bubba Blue", "Jenny Curran"]

            def assign(uid, user="UANALYST"):
                return {"type": "view_submission", "team": {"id": team}, "user": {"id": user}, "view": {
                    "callback_id": ui.ASSIGN_CASE, "private_metadata": json.dumps({"t": ids.t, "c": ids.case}),
                    "state": {"values": {"owner": {"v": {"selected_option": {"value": str(uid)}}}}}}}

            assert (await submit(assign(ids.mate))).status_code == 200
            async with AsyncSessionLocal() as db:
                assert (await db.get(Case, ids.case)).owner_id == ids.mate
            for bad in (999999999, "x"):  # not a member here, or junk
                assert (await submit(assign(bad))).json()["errors"] == {"owner": "Pick an analyst or admin of this tenant."}
            assert (await submit(assign(ids.mate, user="USTRANGER"))).json()["response_action"] == "errors"

            # --- events: URL verification and App Home ---
            chal = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
            assert (await post("/api/v1/slack/events", chal, "application/json")).json() == {"challenge": "abc"}
            assert (await post("/api/v1/slack/events", chal, "application/json", sig=_sign(chal, now, "x"))).status_code == 401
            home = json.dumps({"type": "event_callback", "team_id": team,
                               "event": {"type": "app_home_opened", "tab": "home", "user": "UANALYST"}}).encode()
            assert (await post("/api/v1/slack/events", home, "application/json")).status_code == 200
            assert homes == [(ids.t, "UANALYST")]

        # --- responder alert: posted once to the alert channel, later events thread under it ---
        calls.clear()
        await slack_bot.on_case_event(ids.t, "case.created", made[0].id, {})
        posts = [a for m, a in calls if m == "chat.postMessage"]
        assert len(posts) == 1 and posts[0]["channel"] == "C_ALERTS"
        calls.clear()
        await slack_bot.on_case_event(ids.t, "sla_breach", made[0].id, {"label": "Response SLA"})
        assert any(m == "chat.postMessage" and a.get("thread_ts") for m, a in calls)
        assert any(m == "chat.update" for m, _ in calls)
        calls.clear()
        await slack_bot.on_case_event(ids.t, "case.created", ids.case, {})  # high >= high: alert
        await slack_bot.on_case_event(ids.other, "case.created", ids.foreign, {})  # no Slack there
        assert len([1 for m, _ in calls if m == "chat.postMessage"]) == 1
    finally:
        async with AsyncSessionLocal() as db:
            # Only this test's two tenants: their cases (and rows hanging off them) first, then the tenants.
            case_ids = select(Case.id).where(Case.tenant_id.in_([ids.t, ids.other])).scalar_subquery()
            for model in (TimelineEvent, CaseTriageResult, Notification, CaseFollower):
                await db.execute(delete(model).where(model.case_id.in_(case_ids)))
            await db.execute(delete(Case).where(Case.tenant_id.in_([ids.t, ids.other])))
            await db.execute(delete(Tenant).where(Tenant.id.in_([ids.t, ids.other])))
            await db.execute(delete(User).where(User.id.in_([ids.user, ids.mate])))
            await db.commit()
        await engine.dispose()


def test_slack_bot_end_to_end(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
