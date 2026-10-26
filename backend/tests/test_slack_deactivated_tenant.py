"""Slack never acts in a deactivated tenant: HTTP request URLs, Socket Mode envelopes and queued interactions
mutate nothing (no case created or changed, no suggestions recorded, nothing enqueued)."""
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select, update

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.case import Case, CaseStatus
from app.models.membership import TenantMembership
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.user import User
from app.models.war_room_suggestion import WarRoomSuggestion
from app.services import slack_actions, slack_blocks as ui, slack_bot
from app.utils.crypto import encrypt

SECRET = "deact-signing-secret"


def _sign(body: bytes, ts: str) -> str:
    return "v0=" + hmac.new(SECRET.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()


def _hdr(body: bytes, ctype="application/x-www-form-urlencoded") -> dict:
    ts = str(int(time.time()))
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": _sign(body, ts), "Content-Type": ctype}


def _run(body, monkeypatch):
    import app.tasks.war_room as twr
    import app.tasks.workflows as tw
    from app.tasks import slack as tslack
    enq = []
    monkeypatch.setattr(tw.handle_slack_interaction_task, "delay", lambda *a: enq.append(("interaction", a)))
    monkeypatch.setattr(twr.war_room_message_task, "delay", lambda *a: enq.append(("war_room", a)))
    monkeypatch.setattr(tslack.slack_home_task, "delay", lambda *a: enq.append(("home", a)))
    calls = []

    async def fake_call(token, method, **kw):
        calls.append(method)
        return {"ok": True}

    async def fake_email(token, slack_user_id):
        return email

    monkeypatch.setattr(slack_bot, "slack_call", fake_call)
    monkeypatch.setattr(slack_bot, "_slack_email", fake_email)
    monkeypatch.setattr(slack_actions, "slack_call", fake_call)
    monkeypatch.setattr(slack_actions, "_slack_email", fake_email)

    async def noop(*a, **k):
        calls.append("respond")
    monkeypatch.setattr(slack_actions, "respond", noop)
    monkeypatch.setattr(slack_actions, "_ephemeral", noop)

    h = secrets.token_hex(3)
    team = f"TD{h}".upper()
    email = f"wf-test-{h}-slack@example.com"

    async def go():
        await engine.dispose()
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-slackdeact")
            db.add(t)
            await db.flush()
            u = User(email=email, is_active=True, hashed_password=get_password_hash(secrets.token_urlsafe(16)))
            db.add(u)
            await db.flush()
            db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="admin"))
            integ = SlackIntegration(tenant_id=t.id, team_id=team, bot_token_enc=encrypt("xoxb-x"),
                                     signing_secret_enc=encrypt(SECRET), app_token_enc=encrypt("xapp-x"))
            db.add(integ)
            case = Case(title="deact-case", tenant_id=t.id, status=CaseStatus.NEW)
            db.add(case)
            await db.flush()
            ids = {"t": t.id, "u": u.id, "integ": integ.id, "case": case.id, "team": team}
            await db.execute(update(Tenant).where(Tenant.id == t.id).values(is_active=False))
            await db.commit()
        try:
            await body(ids, enq, calls)
        finally:
            async with AsyncSessionLocal() as db:  # by explicit ids; the cascade helper also clears case history
                from app.utils.tenant_deletion import delete_tenant_cascade
                await db.execute(delete(WarRoomSuggestion).where(WarRoomSuggestion.tenant_id == ids["t"]))
                await db.execute(delete(SlackIntegration).where(SlackIntegration.id == ids["integ"]))
                await delete_tenant_cascade(db, await db.get(Tenant, ids["t"]))
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id == ids["u"]))
                await db.execute(delete(User).where(User.id == ids["u"]))
                await db.commit()
            await engine.dispose()
    asyncio.run(go())


async def _unchanged(ids):
    async with AsyncSessionLocal() as db:
        case = (await db.execute(select(Case).where(Case.id == ids["case"]))).scalars().one()
        assert case.status == CaseStatus.NEW and case.owner_id is None
        others = (await db.execute(select(Case.id).where(Case.tenant_id == ids["t"], Case.id != ids["case"]))).all()
        assert others == []
        assert (await db.execute(select(WarRoomSuggestion.id).where(
            WarRoomSuggestion.tenant_id == ids["t"]))).first() is None


def _submission(ids):
    return {"type": "view_submission", "team": {"id": ids["team"]}, "user": {"id": "UOK"}, "view": {
        "callback_id": ui.CREATE_CASE, "private_metadata": json.dumps({"t": ids["t"]}), "state": {"values": {
            "title": {"v": {"value": "Made in a deactivated tenant"}},
            "severity": {"v": {"selected_option": {"value": "high"}}}}}}}


def _ack_button(ids):
    return {"type": "block_actions", "team": {"id": ids["team"]}, "user": {"id": "UOK"},
            "actions": [{"value": f"case:{ids['t']}:{ids['case']}:ack"}], "response_url": "https://hooks.example/x"}


def _message_event(ids):
    return {"type": "event_callback", "team_id": ids["team"], "event": {
        "type": "message", "user": "UOK", "channel": "C1", "ts": "1.1", "text": "seen 185.220.101.4 on WS-1"}}


def test_http_endpoints_act_on_nothing(monkeypatch):
    async def body(ids, enq, calls):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            form = urlencode({"team_id": ids["team"], "user_id": "UOK", "command": "/sochub", "text": "new x",
                              "trigger_id": "tr"}).encode()
            r = await c.post("/api/v1/slack/commands", content=form, headers=_hdr(form))
            assert r.status_code == 200 and r.json()["text"] == slack_actions.TENANT_DEACTIVATED, r.text
            for payload in (_submission(ids), _ack_button(ids)):
                b = urlencode({"payload": json.dumps(payload)}).encode()
                r = await c.post("/api/v1/slack/interactions", content=b, headers=_hdr(b))
                assert r.status_code == 200, r.text
            b = json.dumps(_message_event(ids)).encode()
            r = await c.post("/api/v1/slack/events", content=b, headers=_hdr(b, "application/json"))
            assert r.status_code == 200, r.text
        assert enq == [] and calls == []  # nothing queued, no modal opened, nothing posted
        await _unchanged(ids)
    _run(body, monkeypatch)


def test_socket_envelopes_act_on_nothing(monkeypatch):
    async def body(ids, enq, calls):
        from app import slack_socket
        cmd = {"type": "slash_commands", "payload": {"team_id": ids["team"], "user_id": "UOK", "command": "/sochub",
                                                     "text": "mine"}}
        assert (await slack_socket.handle_envelope(ids["integ"], cmd))["text"] == slack_actions.TENANT_DEACTIVATED
        assert await slack_socket.handle_envelope(ids["integ"], {"type": "interactive", "payload": _submission(ids)}) is None
        assert await slack_socket.handle_envelope(ids["integ"], {"type": "interactive", "payload": _ack_button(ids)}) is None
        assert await slack_socket.handle_envelope(ids["integ"], {"type": "events_api", "payload": _message_event(ids)}) is None
        assert enq == [] and calls == []
        await _unchanged(ids)
        # and the runner doesn't open a socket for it
        assert ids["integ"] not in await slack_socket._desired()
    _run(body, monkeypatch)


def test_queued_interaction_and_user_resolution_refused(monkeypatch):
    async def body(ids, enq, calls):
        # a block_actions payload queued before the deactivation still changes nothing
        await slack_actions.handle_interaction(ids["t"], _ack_button(ids))
        await _unchanged(ids)
        async with AsyncSessionLocal() as db:
            email = (await db.execute(select(User.email).where(User.id == ids["u"]))).scalar()
            assert await slack_actions._platform_user(db, ids["t"], email) is None
            await db.execute(update(Tenant).where(Tenant.id == ids["t"]).values(is_active=True))
            await db.commit()
            assert (await slack_actions._platform_user(db, ids["t"], email)).id == ids["u"]  # control
    _run(body, monkeypatch)
