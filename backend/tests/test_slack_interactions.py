import asyncio
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.services import slack_actions
from app.utils.crypto import encrypt

SECRET = "int-signing-secret"


def _sign(body: bytes, ts: str, secret=SECRET) -> str:
    return "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()


def _form(payload) -> bytes:
    return urlencode({"payload": json.dumps(payload)}).encode()


async def _scenario(monkeypatch):
    import app.tasks.workflows as tw
    calls = []
    monkeypatch.setattr(tw.handle_slack_interaction_task, "delay", lambda *a: calls.append(a))
    h = secrets.token_hex(3)
    tid = iid = None
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-slackint")
            db.add(t)
            await db.flush()
            tid = t.id
            integ = SlackIntegration(tenant_id=tid, team_id="TTEST", bot_token_enc=encrypt("xoxb-x"),
                                     signing_secret_enc=encrypt(SECRET))
            db.add(integ)
            await db.flush()
            iid = integ.id
            await db.commit()

            ba = {"type": "block_actions", "team": {"id": "TTEST"}, "actions": []}
            now = str(int(time.time()))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                u = "/api/v1/slack/interactions"

                def hdr(body, ts=now, sig=None):
                    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig or _sign(body, ts),
                            "Content-Type": "application/x-www-form-urlencoded"}

                body = _form(ba)
                r = await c.post(u, content=body, headers=hdr(body))
                assert r.status_code == 200 and calls == [(tid, ba)], f"a {r.status_code} {calls}"

                calls.clear()
                r = await c.post(u, content=body, headers=hdr(body, sig=_sign(body, now, "wrong")))
                assert r.status_code == 401 and not calls, "b"

                old = str(int(time.time()) - 400)
                r = await c.post(u, content=body, headers=hdr(body, ts=old))
                assert r.status_code == 401 and not calls, "c"

                unk = _form({**ba, "team": {"id": "TNOPE"}})
                r = await c.post(u, content=unk, headers=hdr(unk))
                assert r.status_code == 401 and not calls, "d"

                r = await c.post(u, content=b"garbage", headers=hdr(b"garbage"))
                assert r.status_code == 400 and not calls, "e"

                r = await c.post(u, content=body, headers={**hdr(body), "X-Slack-Signature": "v0=\u00e9\u00ff".encode("utf-8")})
                assert r.status_code == 401 and not calls, f"f {r.status_code}"

                vs = _form({"type": "view_submission", "team": {"id": "TTEST"}})
                r = await c.post(u, content=vs, headers=hdr(vs))
                assert r.status_code == 200 and not calls, "g"
        finally:
            await db.rollback()
            if iid:
                await db.execute(delete(SlackIntegration).where(SlackIntegration.id == iid))
            if tid:
                await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    async with AsyncSessionLocal() as db:
        n = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
        assert n == 0, "leftover tenants"
    await engine.dispose()


def test_interactions_endpoint(monkeypatch):
    asyncio.run(_scenario(monkeypatch))


def test_cross_tenant_button_is_ignored(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("load_integration must not be called")
    monkeypatch.setattr(slack_actions, "load_integration", boom)
    asyncio.run(slack_actions._handle_case_action(1, {"user": {"id": "U1"}}, 2, 5, "close"))
