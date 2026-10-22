import asyncio
import json
import secrets
from types import SimpleNamespace

from sqlalchemy import delete, select

from app import slack_socket
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.case import Case
from app.models.membership import TenantMembership
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.user import User
from app.services import slack_blocks as ui
from app.services import slack_bot
from app.utils.crypto import decrypt, encrypt


class FakeWS:
    """Plays a scripted Socket Mode session: hello, envelopes, then disconnect."""

    def __init__(self, messages):
        self.messages, self.sent = messages, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for m in self.messages:
            yield json.dumps(m)
            await asyncio.sleep(0.05)  # let envelope acks run, as on a real socket

    async def send(self, raw):
        self.sent.append(json.loads(raw))


class FakeRedis:
    def __init__(self):
        self.kv = {}

    async def set(self, k, v, ex=None):
        self.kv[k] = v


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    team = f"TSOCK{h}"

    async def fake_email(token, slack_user_id):
        return f"sock-{h}@example.com" if slack_user_id == "UOK" else None

    async def fake_call(token, method, **args):
        return {"ok": True, "url": "wss://example.invalid/socket"} if method == "apps.connections.open" else {"ok": True}

    import app.tasks.triage as tt
    import app.workflows.events as ev
    monkeypatch.setattr(slack_bot, "_slack_email", fake_email)
    monkeypatch.setattr(slack_bot, "slack_call", fake_call)
    monkeypatch.setattr(slack_socket, "slack_call", fake_call)
    monkeypatch.setattr(tt.run_case_triage_task, "delay", lambda *a: None)
    monkeypatch.setattr(ev, "emit_event", lambda *a, **k: None)

    async with AsyncSessionLocal() as db:
        t = Tenant(name="sock", slug=f"sock-{h}")
        db.add(t)
        await db.flush()
        u = User(email=f"sock-{h}@example.com", full_name="Bubba Blue", is_active=True,
                 hashed_password=get_password_hash(secrets.token_urlsafe(16)))
        db.add(u)
        await db.flush()
        db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
        integ = SlackIntegration(tenant_id=t.id, team_id=team, bot_token_enc=encrypt("xoxb-x"),
                                 signing_secret_enc=encrypt(secrets.token_urlsafe(32)), app_token_enc=encrypt("xapp-x"))
        db.add(integ)
        await db.commit()
        ids = SimpleNamespace(t=t.id, user=u.id, integ=integ.id)
    try:
        # --- envelope handling ---
        cmd = {"type": "slash_commands", "envelope_id": "e1",
               "payload": {"team_id": team, "user_id": "UOK", "command": "/sochub", "text": "help"}}
        assert "sochub commands" in (await slack_socket.handle_envelope(ids.integ, cmd))["text"]
        foreign = {**cmd, "payload": {**cmd["payload"], "team_id": "TOTHER"}}
        assert await slack_socket.handle_envelope(ids.integ, foreign) is None  # other workspace: dropped
        sub = {"type": "interactive", "envelope_id": "e2", "payload": {
            "type": "view_submission", "team": {"id": team}, "user": {"id": "UOK"}, "view": {
                "callback_id": ui.CREATE_CASE, "private_metadata": json.dumps({"t": ids.t}), "state": {"values": {
                    "title": {"v": {"value": "Socket-made case"}},
                    "severity": {"v": {"selected_option": {"value": "high"}}}}}}}}
        assert (await slack_socket.handle_envelope(ids.integ, sub))["response_action"] == "update"
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(Case.id).where(Case.tenant_id == ids.t, Case.title == "Socket-made case"))).first()

        # --- connection loop: hello -> heartbeat, envelope -> ack with payload, disconnect -> reconnect ---
        ws = FakeWS([{"type": "hello"}, cmd, {"type": "disconnect"}])
        monkeypatch.setattr(slack_socket.websockets, "connect", lambda *a, **k: ws)
        redis = FakeRedis()
        conn = slack_socket.Connection(ids.integ, ids.t, "xapp-x", redis)
        await asyncio.sleep(0.4)
        conn.stop()
        assert ws.sent and ws.sent[0]["envelope_id"] == "e1" and "sochub commands" in ws.sent[0]["payload"]["text"]
        assert slack_socket.heartbeat_key(ids.t) in redis.kv
    finally:
        async with AsyncSessionLocal() as db:
            from app.models.case import TimelineEvent
            from app.models.case_triage import CaseTriageResult
            from app.models.notification import CaseFollower, Notification
            case_ids = select(Case.id).where(Case.tenant_id == ids.t).scalar_subquery()
            for model in (TimelineEvent, CaseTriageResult, Notification, CaseFollower):
                await db.execute(delete(model).where(model.case_id.in_(case_ids)))
            await db.execute(delete(Case).where(Case.tenant_id == ids.t))
            await db.execute(delete(Tenant).where(Tenant.id == ids.t))
            await db.execute(delete(User).where(User.id == ids.user))
            await db.commit()
        await engine.dispose()


def test_socket_mode(monkeypatch):
    asyncio.run(_scenario(monkeypatch))


def test_config_socket_mode_needs_no_signing_secret():
    from tests.audit_fixtures import hdr, run

    async def body(c, w):
        admin = hdr(w, "admin")
        r = await c.put("/api/v1/slack/config", json={"bot_token": "xoxb-1", "app_token": "nope"}, headers=admin)
        assert r.status_code == 422  # app token must be xapp-
        r = await c.put("/api/v1/slack/config", json={"bot_token": "xoxb-1", "app_token": "xapp-1"}, headers=admin)
        assert r.status_code == 200 and r.json()["socket_mode"] is True and r.json()["socket_connected"] is False
        async with AsyncSessionLocal() as db:
            integ = (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == w.ta))).scalars().first()
            assert len(decrypt(integ.signing_secret_enc)) >= 32  # random, never empty (empty HMAC key = forgeable)
        r = await c.put("/api/v1/slack/config", json={"app_token": "-"}, headers=admin)
        assert r.json()["socket_mode"] is False
    run(body)
    asyncio.run(engine.dispose())  # don't hand pooled connections bound to this loop to the next test
