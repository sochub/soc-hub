"""Reason on status/severity changes and the war-room Block Kit cards."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.case import Case, CaseSeverity, CaseStatus, TimelineEvent
from app.models.slack_integration import SlackIntegration
from app.services import slack_bot
from app.services.case_service import apply_case_update
from app.services.slack_blocks import war_room_card
from app.utils.crypto import encrypt
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run
from tests.test_case_milestones import _member

CHANNEL = "CWRC1"
CASE = SimpleNamespace(id=7, title="Phish <!channel>")


def flat(x):
    return json.dumps(x, ensure_ascii=False)


def test_status_card_has_reason_actor_link():
    card = war_room_card("status.changed", CASE, {"from": "open", "to": "in_progress", "reason": "triage done"},
                         actor="Ana")
    blocks = card["attachments"][0]["blocks"]
    text = flat(blocks)
    assert "Status: Open → In progress" in text
    assert "“triage done”" in text or '"triage done"' in text
    assert "Ana" in text and "/cases/7" in text
    assert "<!channel>" not in text
    assert card["attachments"][0]["color"] == "#ecb22e"
    assert "Status" in card["text"]


def test_severity_raise_card_red():
    up = war_room_card("severity.changed", CASE, {"from": "low", "to": "high"}, actor=None)
    assert up["attachments"][0]["color"] == "#e01e5a"
    down = war_room_card("severity.changed", CASE, {"from": "high", "to": "low"}, actor="Bo")
    assert down["attachments"][0]["color"] == "#ecb22e"


def test_owner_and_sla_cards():
    o = war_room_card("owner.changed", CASE, {"from": "Ana", "to": "Bo"}, actor="Cy")
    assert o["attachments"][0]["color"] == "#ecb22e" and "Owner: Ana → Bo" in flat(o)
    s = war_room_card("sla.breached", CASE, {"label": "Response SLA"}, actor=None)
    assert s["attachments"][0]["color"] == "#e01e5a" and "Response SLA" in flat(s)


@pytest.fixture
def posts(monkeypatch):
    calls = []

    async def fake(token, method, **kw):
        calls.append((method, kw))
        return {"ok": True, "user": {"id": "U9"}, "channel": {"id": "D9"}}

    monkeypatch.setattr(slack_bot, "slack_call", fake)
    return calls


def test_reason_appended_to_timeline_and_changes():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            u = await make_user(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            ch = await apply_case_update(db, case=case, user_id=u.id, reason="contained",
                                         update_data={"status": CaseStatus.IN_PROGRESS, "severity": CaseSeverity.CRITICAL})
            await db.commit()
            assert ch["status"]["reason"] == "contained" and ch["severity"]["reason"] == "contained"
            evs = (await db.execute(select(TimelineEvent.content).where(TimelineEvent.case_id == case.id))).scalars().all()
            assert len(evs) == 2 and all(e.endswith(" — contained") for e in evs)
            ch2 = await apply_case_update(db, case=case, user_id=u.id, reason="x", update_data={"title": "new"})
            assert "reason" not in ch2["title"]
    run(body)


def test_reason_too_long_422_and_http_path():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await make_case(db, ids, t)
            _, headers = await _member(db, ids, t)
            cid = case.id
        async with client() as c:
            r = await c.put(f"/api/v1/cases/{cid}", json={"status": "in_progress", "reason": "x" * 301}, headers=headers)
            assert r.status_code == 422
            r = await c.put(f"/api/v1/cases/{cid}", json={"status": "in_progress", "reason": "why not"}, headers=headers)
            assert r.status_code == 200, r.text
        async with AsyncSessionLocal() as db:
            evs = (await db.execute(select(TimelineEvent.content).where(TimelineEvent.case_id == cid))).scalars().all()
            assert any(e.endswith(" — why not") for e in evs)
    run(body)


def test_case_updated_posts_card_not_oneliner(posts):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            u = await make_user(db, ids)
            o2 = await make_user(db, ids)
            case = await make_case(db, ids, t, slack_channel_id=CHANNEL, owner_id=u.id)
            db.add(SlackIntegration(tenant_id=t.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y")))
            await db.commit()
            tid, cid, uid, o2id = t.id, case.id, u.id, o2.id
        changes = {"status": {"from": "open", "to": "in_progress", "reason": "on it", "by": uid},
                   "severity": {"from": "low", "to": "high", "by": uid},
                   "owner_id": {"from": uid, "to": o2id, "by": uid}}
        await slack_bot.on_case_event(tid, "case.updated", cid, changes)
        await slack_bot.on_case_event(tid, "sla_breach", cid, {"label": "Response SLA"})
        war = [kw for m, kw in posts if m == "chat.postMessage" and kw["channel"] == CHANNEL]
        assert len(war) == 4
        assert all(kw.get("attachments") and kw["text"] for kw in war)
        assert all("blocks" not in kw for kw in war)
        text = flat(war)
        assert "Status: Open → In progress" in text and "on it" in text
        assert "Severity: Low → High" in text and "Owner:" in text and "Response SLA" in text
    run(body)
