import asyncio
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.user import User
from app.services.slack_service import SlackError

TOKEN, SECRET = "xoxb-test-token-123", "signing-secret-456"


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(calls, behavior):
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": []}
    state = {"tenant": None, "role": "admin", "user": None}
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-slack")
            user = User(email=f"slack-cfg-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([t, user])
            await db.flush()
            ids["tenants"].append(t.id)
            ids["users"].append(user.id)
            await db.commit()
            state["tenant"], state["user"] = t.id, user

            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_admin] = lambda: state["user"] if state["role"] == "admin" else _deny()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                u = "/api/v1/slack/config"
                r = await c.get(u)
                assert r.status_code == 200 and r.json()["configured"] is False, "a"

                r = await c.put(u, json={"default_channel": "#x"})
                assert r.status_code == 400, "b"

                r = await c.put(u, json={"bot_token": TOKEN, "signing_secret": SECRET, "default_channel": "#soc"})
                assert r.status_code == 200 and r.json()["configured"] is True, "c"
                assert TOKEN not in r.text and SECRET not in r.text, "c leak"
                assert TOKEN not in (await c.get(u)).text, "c leak get"
                async with AsyncSessionLocal() as db2:
                    row = (await db2.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == state["tenant"]))).scalars().first()
                    assert row.bot_token_enc != TOKEN and row.signing_secret_enc != SECRET, "enc"

                r = await c.post(u + "/test")
                assert r.status_code == 200 and r.json() == {"ok": True, "team": "Acme", "team_id": "T123"}, f"d {r.text}"
                assert [x[1] for x in calls] == ["auth.test", "chat.postMessage"] and calls[0][0] == TOKEN, "d calls"
                assert (await c.get(u)).json()["team_id"] == "T123", "d get"

                r = await c.put(u, json={"bot_token": "xoxb-new"})
                assert r.json()["team_id"] is None and r.json()["default_channel"] == "#soc", "e"

                behavior["err"] = SlackError("auth.test: invalid_auth")
                r = await c.post(u + "/test")
                assert r.status_code == 400 and r.json()["detail"] == "auth.test: invalid_auth", "f"

                state["role"] = "viewer"
                assert (await c.get(u)).status_code == 403, "g"
                state["role"] = "admin"

                assert (await c.delete(u)).status_code == 204, "h"
                assert (await c.get(u)).json()["configured"] is False, "h get"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            tids = ids["tenants"]
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            await db.execute(delete(SlackIntegration).where(SlackIntegration.tenant_id.in_(tids)))
            await db.execute(delete(User).where(User.id.in_(ids["users"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            assert left == 0
    await engine.dispose()


def test_slack_config_api(monkeypatch):
    calls, behavior = [], {"err": None}

    async def fake(token, method, **kw):
        calls.append((token, method))
        if behavior["err"]:
            raise behavior["err"]
        return {"team": "Acme", "team_id": "T123"}

    monkeypatch.setattr("app.api.v1.slack.slack_call", fake)
    asyncio.run(_scenario(calls, behavior))
