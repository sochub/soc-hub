"""Opening a private war room from the case, and the listen toggle."""
import asyncio
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.membership import TenantMembership
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.user import User
from app.services import slack_actions as sa
from app.services import slack_bot
from app.services.slack_service import SlackError
from app.services.war_room_naming import collision_name
from app.utils.crypto import encrypt


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


@asynccontextmanager
async def world(monkeypatch, *, slack_id="UME", create_errors=()):
    await engine.dispose()
    h = secrets.token_hex(3)
    calls = []
    errors = list(create_errors)

    async def fake_call(token, method, **kw):
        calls.append((method, kw))
        if method == "conversations.create":
            if errors:
                raise errors.pop(0)
            return {"channel": {"id": f"C{h}"}}
        return {"ok": True}

    async def fake_slack_id(token, email):
        return slack_id

    monkeypatch.setattr(slack_bot, "slack_call", fake_call)
    monkeypatch.setattr(slack_bot, "_slack_id", fake_slack_id)
    state = {"tenant": None, "role": "analyst", "user": None}
    monkeypatch.setattr(deps, "_active_role", lambda u: state["role"])
    w = {"calls": calls, "state": state, "t": [], "u": [], "c": [], "i": []}
    async with AsyncSessionLocal() as db:
        try:
            t1, t2 = Tenant(name="wr-test", slug=f"wr-{h}-a"), Tenant(name="wr-test", slug=f"wr-{h}-b")
            user = User(email=f"wr-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([t1, t2, user])
            await db.flush()
            w["t"] = [t1.id, t2.id]
            w["u"] = [user.id]
            integ = SlackIntegration(tenant_id=t1.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y"))
            case = Case(title="Compromised Account!", tenant_id=t1.id)
            other = Case(title="other tenant", tenant_id=t2.id)
            db.add_all([integ, case, other, TenantMembership(user_id=user.id, tenant_id=t1.id, role="analyst")])
            await db.flush()
            w.update(i=[integ.id], c=[case.id, other.id], case_id=case.id, other_id=other.id, user=user, db=db, h=h)
            await db.commit()
            state["tenant"], state["user"] = t1.id, user
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                w["client"] = c
                yield w
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            await db.execute(delete(AuditLog).where(AuditLog.entity_type == "case", AuditLog.entity_id.in_(w["c"])))
            await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(w["c"])))
            await db.execute(delete(Case).where(Case.id.in_(w["c"])))
            await db.execute(delete(SlackIntegration).where(SlackIntegration.id.in_(w["i"])))
            await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(w["u"])))
            await db.execute(delete(User).where(User.id.in_(w["u"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(w["t"])))
            await db.commit()


def _creates(w):
    return [kw for m, kw in w["calls"] if m == "conversations.create"]


def test_open_from_case_private_named_and_listening(monkeypatch):
    async def go():
        async with world(monkeypatch) as w:
            r = await w["client"].post(f"/api/v1/cases/{w['case_id']}/war-room", json={"listen": True})
            assert r.status_code == 200, r.text
            body = r.json()
            cr = _creates(w)[0]
            assert cr["is_private"] is True and cr["name"] == f"inc-{w['case_id']:04d}-compromised-account"
            assert body["channel_name"] == cr["name"] and body["listen"] is True
            assert body["url"] == f"https://app.slack.com/client/TX/{body['channel_id']}"
            async with AsyncSessionLocal() as db:
                c = (await db.execute(select(Case).where(Case.id == w["case_id"]))).scalars().one()
                assert c.slack_listen is True and c.slack_channel_name == cr["name"]
                assert c.slack_channel_created_at is not None
            g = (await w["client"].get(f"/api/v1/cases/{w['case_id']}")).json()
            assert g["slack_channel_id"] == body["channel_id"] and g["slack_listen"] is True
            assert g["war_room_url"] == body["url"] and g["war_room_silent"] is False
    asyncio.run(go())


def test_open_with_custom_name_is_sanitised(monkeypatch):
    async def go():
        async with world(monkeypatch) as w:
            r = await w["client"].post(f"/api/v1/cases/{w['case_id']}/war-room", json={"name": "My Room!! #1", "listen": False})
            assert r.status_code == 200 and _creates(w)[0]["name"] == "my-room-1", r.text
    asyncio.run(go())


def test_empty_name_400(monkeypatch):
    async def go():
        async with world(monkeypatch) as w:
            r = await w["client"].post(f"/api/v1/cases/{w['case_id']}/war-room", json={"name": "!!!", "listen": False})
            assert r.status_code == 400 and not _creates(w)
    asyncio.run(go())


def test_open_without_clicker_slack_id_invites_others(monkeypatch):
    async def go():
        async with world(monkeypatch, slack_id=None) as w:
            r = await w["client"].post(f"/api/v1/cases/{w['case_id']}/war-room", json={"listen": False})
            assert r.status_code == 200, r.text
            invites = [kw for m, kw in w["calls"] if m == "conversations.invite"]
            assert all("None" not in kw["users"] for kw in invites)
    asyncio.run(go())


def test_open_name_taken_uses_suffix(monkeypatch):
    async def go():
        async with world(monkeypatch, create_errors=[SlackError("x", code="name_taken")]) as w:
            r = await w["client"].post(f"/api/v1/cases/{w['case_id']}/war-room", json={"listen": False})
            assert r.status_code == 200, r.text
            cr = _creates(w)
            assert len(cr) == 2 and cr[1]["name"] == collision_name(cr[0]["name"], 2) and cr[1]["is_private"] is True
    asyncio.run(go())


def test_missing_scope_names_groups_write(monkeypatch):
    async def go():
        async with world(monkeypatch, create_errors=[SlackError("x", code="missing_scope")]) as w:
            r = await w["client"].post(f"/api/v1/cases/{w['case_id']}/war-room", json={"listen": False})
            assert r.status_code == 400 and "groups:write" in r.json()["detail"]
    asyncio.run(go())


def test_open_twice_409(monkeypatch):
    async def go():
        async with world(monkeypatch) as w:
            url = f"/api/v1/cases/{w['case_id']}/war-room"
            assert (await w["client"].post(url, json={"listen": False})).status_code == 200
            assert (await w["client"].post(url, json={"listen": False})).status_code == 409
    asyncio.run(go())


def test_viewer_403(monkeypatch):
    async def go():
        async with world(monkeypatch) as w:
            w["state"]["role"] = "viewer"
            r = await w["client"].post(f"/api/v1/cases/{w['case_id']}/war-room", json={"listen": False})
            assert r.status_code == 403 and not _creates(w)
    asyncio.run(go())


def test_cross_tenant_404(monkeypatch):
    async def go():
        async with world(monkeypatch) as w:
            r = await w["client"].post(f"/api/v1/cases/{w['other_id']}/war-room", json={"listen": False})
            assert r.status_code == 404 and not _creates(w)
            assert (await w["client"].patch(f"/api/v1/cases/{w['other_id']}/war-room", json={"listen": True})).status_code == 404
    asyncio.run(go())


def test_patch_listen_audited(monkeypatch):
    async def go():
        async with world(monkeypatch) as w:
            base = f"/api/v1/cases/{w['case_id']}/war-room"
            assert (await w["client"].patch(base, json={"listen": True})).status_code == 409  # no room yet
            await w["client"].post(base, json={"listen": False})
            r = await w["client"].patch(base, json={"listen": True})
            assert r.status_code == 200 and r.json()["listen"] is True, r.text
            async with AsyncSessionLocal() as db:
                c = (await db.execute(select(Case).where(Case.id == w["case_id"]))).scalars().one()
                assert c.slack_listen is True
                logs = (await db.execute(select(AuditLog).where(
                    AuditLog.entity_id == w["case_id"], AuditLog.action == "war_room_listen_changed"))).scalars().all()
                assert len(logs) == 1 and logs[0].tenant_id == w["t"][0]
    asyncio.run(go())


def test_war_room_silent_flag(monkeypatch):
    from datetime import datetime, timedelta, timezone

    async def go():
        async with world(monkeypatch) as w:
            await w["client"].post(f"/api/v1/cases/{w['case_id']}/war-room", json={"listen": True})
            url = f"/api/v1/cases/{w['case_id']}"
            async with AsyncSessionLocal() as db:
                c = (await db.execute(select(Case).where(Case.id == w["case_id"]))).scalars().one()
                c.slack_channel_created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
                await db.commit()
            assert (await w["client"].get(url)).json()["war_room_silent"] is True
            async with AsyncSessionLocal() as db:
                i = (await db.execute(select(SlackIntegration).where(SlackIntegration.id == w["i"][0]))).scalars().one()
                i.last_message_event_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                await db.commit()
            assert (await w["client"].get(url)).json()["war_room_silent"] is False
    asyncio.run(go())


def test_slack_button_path_listen_off(monkeypatch):
    async def go():
        async with world(monkeypatch) as w:
            async def fake_sa_call(token, method, **kw):
                return {"user": {"profile": {"email": w["user"].email}}}

            async def noop(*a, **k):
                return None
            monkeypatch.setattr(sa, "slack_call", fake_sa_call)
            monkeypatch.setattr(sa, "_ephemeral", noop)
            await sa._handle_case_action(w["t"][0], {"user": {"id": "U1"}}, w["t"][0], w["case_id"], "warroom")
            assert _creates(w) and _creates(w)[0]["is_private"] is True
            async with AsyncSessionLocal() as db:
                c = (await db.execute(select(Case).where(Case.id == w["case_id"]))).scalars().one()
                assert c.slack_channel_id and c.slack_listen is False
    asyncio.run(go())
