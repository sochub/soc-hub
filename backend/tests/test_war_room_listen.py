"""Listening pipeline: Slack message events -> suggestions -> thread reply."""
import secrets
import time

import pytest
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.slack_integration import SlackIntegration
from app.models.war_room_suggestion import WarRoomMessageSeen, WarRoomSuggestion
from app.services import slack_bot
from app.services.slack_blocks import suggestions_reply
from app.services.war_room_extract import Candidate
from app.tasks import war_room as wr
from app.utils.crypto import encrypt
from tests.guest_fixtures import make_case, make_tenant, make_user, run

TEXT = "seen 185.220.101.4 on WS-FIN-07, contained at 14:32"


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, k):
        return self.data.get(k)

    async def set(self, k, v, ex=None):
        self.data[k] = v

    async def aclose(self):
        pass


@pytest.fixture
def slack(monkeypatch):
    calls = []
    state = {"tz": "Europe/Madrid", "email": None}

    async def fake_call(token, method, **kw):
        calls.append((method, kw))
        if method == "users.info":
            return {"user": {"tz": state["tz"], "profile": {"email": state["email"]}}}
        if method == "chat.postMessage":
            return {"ok": True, "ts": "9.9"}
        if method == "chat.getPermalink":
            return {"ok": True, "permalink": "https://x.slack.com/archives/C/p1"}
        return {"ok": True}

    monkeypatch.setattr(wr, "slack_call", fake_call)
    r = FakeRedis()
    monkeypatch.setattr(wr, "_redis", lambda: r)
    return {"calls": calls, "state": state, "redis": r}


def posts(slack):
    return [kw for m, kw in slack["calls"] if m == "chat.postMessage"]


async def room(db, ids, *, listen=True, channel=None, alert_channel=None):
    t = await make_tenant(db, ids)
    channel = channel or f"C{secrets.token_hex(4).upper()}"
    case = await make_case(db, ids, t, slack_channel_id=channel, slack_listen=listen)
    integ = SlackIntegration(tenant_id=t.id, team_id="TX", bot_token_enc=encrypt("x"),
                             signing_secret_enc=encrypt("y"), alert_channel=alert_channel)
    db.add(integ)
    await db.commit()
    return t, case, channel


def ev(channel, ts=None, **kw):
    ts = ts or f"{time.time():.6f}"
    return {"channel": channel, "ts": ts, "thread_ts": None, "user": "U1", "text": TEXT, **kw}


async def rows(case_id):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(WarRoomSuggestion).where(WarRoomSuggestion.case_id == case_id))).scalars().all()


async def seen(channel):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(WarRoomMessageSeen).where(
            WarRoomMessageSeen.slack_channel == channel))).scalar()


def test_message_event_creates_suggestions_and_thread_reply(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        e = ev(ch)
        await wr.process_message(t.id, e)
        rs = await rows(case.id)
        assert sorted(r.kind for r in rs) == ["artifact", "artifact", "milestone"]
        assert all(r.status == "pending" and r.reply_ts == "9.9" and r.permalink for r in rs)
        (p,) = posts(slack)
        assert p["thread_ts"] == e["ts"] and p["channel"] == ch and p["text"] == "SOC-bot suggestions"
        assert p["blocks"]
    run(body)


def test_reply_threads_under_parent_thread(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        await wr.process_message(t.id, ev(ch, thread_ts="1.000001"))
        assert posts(slack)[0]["thread_ts"] == "1.000001"
    run(body)


def test_message_event_retry_single_reply(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        e = ev(ch)
        await wr.process_message(t.id, e)
        await wr.process_message(t.id, e)
        assert len(posts(slack)) == 1 and len(await rows(case.id)) == 3
    run(body)


def test_listen_off_ignored(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids, listen=False)
        await wr.process_message(t.id, ev(ch))
        assert await rows(case.id) == [] and not posts(slack) and await seen(ch) == 0
    run(body)


def test_message_event_foreign_channel_ignored(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids, alert_channel="CALERT1")
            t2, case2, ch2 = await room(db, ids)
        await wr.process_message(t.id, ev("CALERT1"))
        await wr.process_message(t.id, ev(ch2))  # another tenant's war room
        assert await rows(case.id) == [] and await rows(case2.id) == []
        assert not posts(slack) and await seen("CALERT1") == 0 and await seen(ch2) == 0
    run(body)


def test_nothing_new_no_reply(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        await wr.process_message(t.id, ev(ch, ts="1.1"))
        n = len(posts(slack))
        await wr.process_message(t.id, ev(ch, ts="2.2"))  # same values, new message
        assert n == 1 and len(posts(slack)) == 1 and len(await rows(case.id)) == 3
        await wr.process_message(t.id, ev(ch, ts="3.3", text="hello team, any news?"))
        assert len(posts(slack)) == 1
    run(body)


def test_last_message_event_at_updated(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        await wr.process_message(t.id, ev(ch, text="nothing here"))
        async with AsyncSessionLocal() as db:
            integ = (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == t.id))).scalars().one()
            assert integ.last_message_event_at is not None
    run(body)


def test_tz_cached_in_redis_and_linked_user_fallback(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
            u = await make_user(db, ids)
            u.timezone = "Asia/Tokyo"
            await db.commit()
        slack["state"]["tz"] = None
        slack["state"]["email"] = u.email
        await wr.process_message(t.id, ev(ch, ts="1.1"))
        await wr.process_message(t.id, ev(ch, ts="2.2", text="contained at 14:32"))
        assert sum(1 for m, _ in slack["calls"] if m == "users.info") == 1
        assert any(k == f"slack:tz:{t.id}:U1" for k in slack["redis"].data)
        ms = [r for r in await rows(case.id) if r.kind == "milestone"]
        assert ms and ms[0].payload["at"].endswith("+09:00")
    run(body)


def test_author_email_excluded(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        slack["state"]["email"] = "me@corp.example"
        await wr.process_message(t.id, ev(ch, text="mail from me@corp.example and evil@bad.example"))
        vals = [r.payload.get("value") for r in await rows(case.id)]
        assert "me@corp.example" not in vals
    run(body)


def test_slack_failure_releases_seen_row(slack, monkeypatch):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        orig = wr.slack_call

        async def boom(token, method, **kw):
            if method == "chat.postMessage":
                raise wr.SlackError("down")
            return await orig(token, method, **kw)
        monkeypatch.setattr(wr, "slack_call", boom)
        await wr.process_message(t.id, ev(ch))  # reply failure must not raise or lose rows
        assert len(await rows(case.id)) == 3
    run(body)


# ---- dispatch_event filter ---------------------------------------------------

@pytest.fixture
def delays(monkeypatch):
    calls = []
    monkeypatch.setattr(wr.war_room_message_task, "delay", lambda *a: calls.append(a))
    return calls


def _integ():
    return SlackIntegration(id=1, tenant_id=7)


def _msg(**kw):
    return {"event": {"type": "message", "channel": "C1", "ts": "1.1", "user": "U1", "text": "hi", **kw}}


def test_dispatch_forwards_plain_message(delays):
    slack_bot.dispatch_event(_integ(), _msg(thread_ts="0.5"))
    assert delays == [(7, {"channel": "C1", "ts": "1.1", "thread_ts": "0.5", "user": "U1", "text": "hi"})]


def test_dispatch_forwards_thread_broadcast(delays):
    slack_bot.dispatch_event(_integ(), _msg(subtype="thread_broadcast"))
    assert len(delays) == 1


@pytest.mark.parametrize("extra", [
    {"bot_id": "B1"}, {"subtype": "message_changed"}, {"subtype": "channel_join"},
    {"subtype": "bot_message"}, {"user": None}, {"text": ""}, {"channel": None}, {"ts": None},
])
def test_bot_and_edited_messages_ignored(delays, extra):
    slack_bot.dispatch_event(_integ(), _msg(**extra))
    assert delays == []


# ---- blocks ------------------------------------------------------------------

def _sug(id, kind, payload, status="pending", by=None):
    return WarRoomSuggestion(id=id, kind=kind, payload=payload, status=status, resolved_by_user_id=by)


def test_suggestions_reply_blocks():
    s = [_sug(1, "artifact", {"type": "ip", "value": "1.2.3.4"}),
         _sug(2, "artifact", {"type": "hostname", "value": "WS<1>"}),
         _sug(3, "artifact", {"type": "username", "value": "corp\\bob"}),
         _sug(4, "milestone", {"key": "contained", "at": "2026-10-26T14:32:00+01:00"})]
    blocks = suggestions_reply(s, 7)
    text = " ".join(b["text"]["text"] for b in blocks if b["type"] == "section")
    assert "IP `1.2.3.4`" in text and "Host `WS&lt;1&gt;`" in text and "User `corp\\bob`" in text
    assert "Set *Contained* to Mon 26 Oct 14:32" in text
    vals = [e["value"] for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert "wrs:7:1:a" in vals and "wrs:7:1:d" in vals and "wrs:7:4:a" in vals


def test_suggestions_reply_resolved_rows():
    s = [_sug(1, "artifact", {"type": "ip", "value": "1.2.3.4"}, "accepted", 5),
         _sug(2, "artifact", {"type": "ip", "value": "5.6.7.8"}, "dismissed"),
         _sug(3, "milestone", {"key": "contained", "at": "2026-10-26T14:32:00+00:00"}, "conflict")]
    blocks = suggestions_reply(s, 7, names={5: "Ana"})
    ctx = " ".join(e["text"] for b in blocks if b["type"] == "context" for e in b["elements"])
    assert "✓ added by Ana" in ctx and "dismissed" in ctx and "⚠ conflicts with Detected" in ctx
    assert not [b for b in blocks if b["type"] == "actions"]
