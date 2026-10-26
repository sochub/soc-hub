import asyncio
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.crypto import encrypt


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario():
    h = secrets.token_hex(3)
    tids, uids = [], []
    state = {"tenant": None, "role": "analyst", "user": None}
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-wrs")
            u = User(email=f"wrs-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([t, u])
            await db.flush()
            tids.append(t.id)
            uids.append(u.id)
            await db.commit()
            state["tenant"], state["user"] = t.id, u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: state["user"] if state["role"] == "analyst" else _deny()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                url = "/api/v1/slack/war-room-settings"
                r = await c.get(url)
                assert r.status_code == 200 and r.json() == {"configured": False, "war_room_name_template": None}, r.text

                db.add(SlackIntegration(tenant_id=t.id, bot_token_enc=encrypt("xoxb-x"), signing_secret_enc=encrypt("s"),
                                        team_id="T1", default_channel="#soc", war_room_name_template="sec-{id}"))
                await db.commit()
                r = await c.get(url)
                assert r.status_code == 200 and r.json() == {"configured": True, "war_room_name_template": "sec-{id}"}, r.text

                state["role"] = "viewer"
                assert (await c.get(url)).status_code == 403
        finally:
            app.dependency_overrides.pop(deps.get_effective_tenant_id, None)
            app.dependency_overrides.pop(deps.require_analyst_or_above, None)
            await db.rollback()
            await db.execute(delete(SlackIntegration).where(SlackIntegration.tenant_id.in_(tids)))
            await db.execute(delete(User).where(User.id.in_(uids)))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
    await engine.dispose()


def test_war_room_settings():
    asyncio.run(_scenario())
