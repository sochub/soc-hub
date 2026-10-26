"""Slack Add/Set/Dismiss buttons on SOC-bot suggestions and the in-place re-render of the reply."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.artifact import ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.slack_integration import SlackIntegration
from app.models.war_room_suggestion import WarRoomSuggestion
from app.services import slack_actions as sa
from app.services import slack_bot
from app.services.slack_blocks import suggestions_reply
from app.services.war_room_extract import Candidate
from app.services.slack_service import parse_action_value
from app.utils.crypto import encrypt
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run
from tests.test_case_milestones import _member
from tests.test_war_room_suggestions import art, rec

CHANNEL = "CWRB1"
N = SimpleNamespace  # plain ids: ORM instances expire on commit


@pytest.fixture
def env(monkeypatch):
    e = {"email": None, "ephem": [], "updates": [], "delays": []}

    async def fake_sa_call(token, method, **kw):
        return {"user": {"profile": {"email": e["email"]}}}

    async def fake_bot_call(token, method, **kw):
        if method == "chat.update":
            e["updates"].append(kw)
        return {"ok": True}

    async def fake_ephemeral(payload, text):
        e["ephem"].append(text)

    monkeypatch.setattr(sa, "slack_call", fake_sa_call)
    monkeypatch.setattr(sa, "_ephemeral", fake_ephemeral)
    monkeypatch.setattr(slack_bot, "slack_call", fake_bot_call)
    from app.tasks.slack import slack_case_event_task
    monkeypatch.setattr(slack_case_event_task, "delay", lambda *a: e["delays"].append(a))
    return e


async def drain(e):
    calls, e["delays"] = list(e["delays"]), []
    for a in calls:
        await slack_bot.on_case_event(*a)
    return calls


async def setup(db, ids, env_):
    t = await make_tenant(db, ids)
    tid = t.id
    case = await db.get(Case, (await make_case(db, ids, t, slack_channel_id=CHANNEL)).id)
    db.add(SlackIntegration(tenant_id=tid, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y")))
    await db.commit()
    u, headers = await _member(db, ids, t)
    u_email = u.email
    env_["email"] = u_email
    (s,) = await rec(db, case, [art("1.2.3.4")], channel=CHANNEL)
    s.reply_ts = "5.5"
    await db.commit()
    return (N(id=tid), N(id=case.id), N(id=u.id, email=u_email), headers, N(id=s.id))


def payload():
    return {"user": {"id": "U1"}, "response_url": "https://hooks.slack.com/x"}


def flat(blocks):
    return json.dumps(blocks)


def test_parse_wrs_values():
    assert parse_action_value("wrs:12:345:a") == ("wrs", 12, 345, "accept")
    assert parse_action_value("wrs:12:345:d") == ("wrs", 12, 345, "dismiss")
    assert parse_action_value("wrs:12:345:x") is None
    assert parse_action_value("wrs:12:345") is None


def test_slack_accept_creates_artifact_and_updates_reply(env):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, u, _, s = await setup(db, ids, env)
            await sa.handle_interaction(t.id, {**payload(), "actions": [{"value": f"wrs:{t.id}:{s.id}:a"}]})
            await drain(env)
            db.expire_all()
            assert (await db.execute(select(func.count()).select_from(CaseArtifact).where(
                CaseArtifact.case_id == case.id))).scalar() == 1
            row = (await db.execute(select(WarRoomSuggestion).where(WarRoomSuggestion.id == s.id))).scalars().one()
            assert row.status == "accepted" and row.resolved_by_user_id == u.id
            audit = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == t.id, AuditLog.action == "war_room_suggestion_accepted"))).scalars().one()
            assert audit.changes["slack_user_id"] == "U1"
        assert env["ephem"] == []
        (up,) = env["updates"]
        assert up["channel"] == CHANNEL and up["ts"] == "5.5" and up["text"] == "SOC-bot suggestions"
        assert "added by" in flat(up["blocks"]) and "actions" not in flat(up["blocks"])
    run(body)


def test_slack_dismiss(env):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, u, _, s = await setup(db, ids, env)
            await sa.handle_interaction(t.id, {**payload(), "actions": [{"value": f"wrs:{t.id}:{s.id}:d"}]})
            await drain(env)
            assert env["ephem"] == []
            assert len(env["updates"]) == 1 and "dismissed" in flat(env["updates"][0]["blocks"])
            assert (await db.execute(select(func.count()).select_from(CaseArtifact).where(
                CaseArtifact.case_id == case.id))).scalar() == 0
    run(body)


def test_slack_click_wrong_tenant_ignored(env):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, u, _, s = await setup(db, ids, env)
            await sa._handle_suggestion_action(t.id, payload(), t.id + 999, s.id, "accept")
            db.expire_all()
            row = (await db.execute(select(WarRoomSuggestion).where(WarRoomSuggestion.id == s.id))).scalars().one()
            assert row.status == "pending" and env["delays"] == [] and env["ephem"] == []
    run(body)


def test_unlinked_or_viewer_gets_ephemeral_no_change(env):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, u, _, s = await setup(db, ids, env)
            viewer, _h = await _member(db, ids, t, role="viewer")
            for email in (None, "nobody@example.test", viewer.email):
                env["email"] = email
                await sa._handle_suggestion_action(t.id, payload(), t.id, s.id, "accept")
            assert len(env["ephem"]) == 3 and all("isn't linked" in m for m in env["ephem"])
            db.expire_all()
            row = (await db.execute(select(WarRoomSuggestion).where(WarRoomSuggestion.id == s.id))).scalars().one()
            assert row.status == "pending" and env["delays"] == [] and env["updates"] == []
    run(body)


def test_slack_click_on_unknown_suggestion_is_ephemeral(env):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, u, _, s = await setup(db, ids, env)
            await sa._handle_suggestion_action(t.id, payload(), t.id, s.id + 100000, "accept")
            assert len(env["ephem"]) == 1 and "no longer" in env["ephem"][0]
    run(body)


def test_ui_accept_rerenders_slack_reply(env):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, u, headers, s = await setup(db, ids, env)
            async with client() as c:
                r = await c.post(f"/api/v1/cases/{case.id}/war-room/suggestions/{s.id}/accept", headers=headers)
            assert r.status_code == 200, r.text
            calls = await drain(env)
            assert len(calls) == 1 and calls[0][1] == "war_room.rerender"
            (up,) = env["updates"]
            assert up["ts"] == "5.5" and "added by" in flat(up["blocks"])
    run(body)


def test_racing_slack_and_ui_apply_once(env):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, u, headers, s = await setup(db, ids, env)
            async with client() as c:
                r = await c.post(f"/api/v1/cases/{case.id}/war-room/suggestions/{s.id}/accept", headers=headers)
            assert r.status_code == 200
            await sa._handle_suggestion_action(t.id, payload(), t.id, s.id, "accept")  # late Slack click
            await drain(env)
            db.expire_all()
            assert (await db.execute(select(func.count()).select_from(CaseArtifact).where(
                CaseArtifact.case_id == case.id))).scalar() == 1
            assert (await db.execute(select(func.count()).select_from(AuditLog).where(
                AuditLog.tenant_id == t.id, AuditLog.action == "war_room_suggestion_accepted"))).scalar() == 1
            assert len(env["updates"]) == 1  # the no-op click queued no second re-render
            assert env["ephem"] == ["This suggestion was already resolved."]
    run(body)


def test_reply_backtick_value_cannot_break_code_span():
    s = WarRoomSuggestion(id=1, kind="artifact", payload={"type": "domain", "value": "a`b.com"}, status="pending")
    text = json.dumps(suggestions_reply([s], 1))
    assert "a`b.com" not in text and "aˋb.com" in json.dumps(suggestions_reply([s], 1), ensure_ascii=False)


def test_slack_milestone_conflict_no_ephemeral(env):
    async def body(ids):
        from datetime import datetime, timezone
        async with AsyncSessionLocal() as db:
            t, case, u, _, s = await setup(db, ids, env)
            c = await db.get(Case, case.id)
            c.detected_at = datetime(2099, 12, 1, tzinfo=timezone.utc)
            await db.commit()
            (m,) = await rec(db, c, [Candidate("milestone", {"key": "contained", "at": "2099-10-22T18:40:00+00:00"}, "contained")],
                             channel=CHANNEL)
            m.reply_ts = "6.6"
            await db.commit()
            await sa._handle_suggestion_action(t.id, payload(), t.id, m.id, "accept")
            await drain(env)
            assert env["ephem"] == [] and "conflicts" in flat(env["updates"][0]["blocks"])
    run(body)
