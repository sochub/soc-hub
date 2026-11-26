"""Listening pipeline: Slack message events -> suggestions -> thread reply."""
import itertools
import json
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.membership import TenantMembership
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
            return {"user": {"tz": state["tz"], "real_name": "Ana Ruiz", "profile": {"email": state["email"]}}}
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


# A fixed clock (16:00 in Madrid): TEXT's "contained at 14:32" must be in the past whenever the suite runs.
_clock = itertools.count(int(datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc).timestamp()))


def ev(channel, ts=None, **kw):
    ts = ts or f"{next(_clock)}.000100"
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
        await wr.process_message(t.id, ev(ch))
        await wr.process_message(t.id, ev(ch, text="contained at 14:32"))
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


def _failing_post(monkeypatch, error):
    orig = wr.slack_call

    async def boom(token, method, **kw):
        if method == "chat.postMessage":
            raise error
        return await orig(token, method, **kw)
    monkeypatch.setattr(wr, "slack_call", boom)
    return orig


def test_transient_reply_failure_keeps_rows_and_the_retry_posts_once(slack, monkeypatch):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        e = ev(ch)
        orig = _failing_post(monkeypatch, wr.SlackError("down"))
        with pytest.raises(wr.SlackError):   # raised so Celery retries the task
            await wr.process_message(t.id, e)
        rs = await rows(case.id)
        assert len(rs) == 3 and all(r.reply_ts is None and r.permalink for r in rs) and await seen(ch) == 1
        monkeypatch.setattr(wr, "slack_call", orig)
        await wr.process_message(t.id, e)   # the retry: nothing is recorded twice, the reply goes out
        await wr.process_message(t.id, e)   # a late redelivery: no second reply
        rs = await rows(case.id)
        assert len(rs) == 3 and all(r.reply_ts == "9.9" for r in rs) and len(posts(slack)) == 1
    run(body)


def test_permanent_reply_failure_does_not_raise_or_lose_rows(slack, monkeypatch):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        _failing_post(monkeypatch, wr.SlackError("chat.postMessage: is_archived", code="is_archived"))
        await wr.process_message(t.id, ev(ch))
        assert len(await rows(case.id)) == 3
    run(body)


def test_crash_while_recording_leaves_nothing_behind(slack, monkeypatch):
    """'Seen' and the suggestions commit together: a failed run is redone in full by the retry."""
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        e = ev(ch)
        real = wr.record_candidates

        async def boom(*a, **kw):
            await real(*a, **kw)
            raise RuntimeError("worker died")
        monkeypatch.setattr(wr, "record_candidates", boom)
        with pytest.raises(RuntimeError):
            await wr.process_message(t.id, e)
        assert await rows(case.id) == [] and await seen(ch) == 0 and not posts(slack)
        monkeypatch.setattr(wr, "record_candidates", real)
        await wr.process_message(t.id, e)
        assert len(await rows(case.id)) == 3 and len(posts(slack)) == 1
    run(body)


def test_task_retries_with_backoff():
    task = wr.war_room_message_task
    assert task.max_retries == 3 and task.retry_backoff is True and task.acks_late
    assert task.autoretry_for == (Exception,) and task.dont_autoretry_for == (wr.SoftTimeLimitExceeded,)


def test_overflow_reply_shows_five_and_counts_the_rest(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        text = " ".join(f"185.220.101.{i}" for i in range(1, 9)) + " contained at 14:32"
        await wr.process_message(t.id, ev(ch, text=text))
        rs = await rows(case.id)
        assert len(rs) == 9 and all(r.status == "pending" and r.permalink for r in rs)
        assert sorted(r.kind for r in rs if r.reply_ts) == ["artifact"] * 5 + ["milestone"]
        blocks = json.dumps(posts(slack)[0]["blocks"])
        assert "+3 more in" in blocks and "wrs_accept_all" in blocks and "185.220.101.8" not in blocks
    run(body)


def test_author_and_permalink_are_stored(slack):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        await wr.process_message(t.id, ev(ch, text="beacon to 185.220.101.4"))
        (r,) = await rows(case.id)
        assert r.payload == {"type": "ip", "value": "185.220.101.4", "slack_user": "U1", "author": "Ana Ruiz"}
        assert r.permalink == "https://x.slack.com/archives/C/p1"
    run(body)


def test_users_info_failure_falls_back_to_bound_member_timezone(slack, monkeypatch):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
            u = await make_user(db, ids)
            u.timezone = "Asia/Tokyo"
            db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst", slack_user_id="U1"))
            await db.commit()
        orig = wr.slack_call

        async def no_profile(token, method, **kw):
            if method == "users.info":
                raise wr.SlackError("users.info: user_not_found", code="user_not_found")
            return await orig(token, method, **kw)
        monkeypatch.setattr(wr, "slack_call", no_profile)
        await wr.process_message(t.id, ev(ch))
        ms = [r for r in await rows(case.id) if r.kind == "milestone"]
        assert ms and ms[0].payload["at"].endswith("+09:00")
        # an author nobody can place: indicators are still suggested, a zone-less time is not guessed as UTC
        await wr.process_message(t.id, ev(ch, user="U2", text="recovered at 15:10, also 185.220.101.99"))
        new = [r for r in await rows(case.id) if r.payload.get("slack_user") == "U2"]
        assert [r.kind for r in new] == ["artifact"]
    run(body)


@pytest.mark.parametrize("status", ["resolved", "closed"])
def test_finished_case_gets_no_suggestions(slack, status):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
            c = await db.get(Case, case.id)
            c.status = status
            await db.commit()
        await wr.process_message(t.id, ev(ch))
        assert await rows(case.id) == [] and not posts(slack) and await seen(ch) == 0
        async with AsyncSessionLocal() as db:
            assert (await db.get(Case, case.id)).slack_listen is True   # the toggle is left alone
    run(body)


def test_accepted_before_reply_ts_is_stored_redraws_the_reply(slack, monkeypatch):
    async def body(ids):
        from app.tasks.slack import slack_case_event_task
        queued = []
        monkeypatch.setattr(slack_case_event_task, "delay", lambda *a: queued.append(a))
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
        orig = wr.slack_call

        async def accept_meanwhile(token, method, **kw):
            if method == "chat.postMessage":   # someone accepts in the web UI while the reply is in flight
                async with AsyncSessionLocal() as db:
                    row = (await db.execute(select(WarRoomSuggestion).where(
                        WarRoomSuggestion.case_id == case.id))).scalars().first()
                    row.status = "accepted"
                    await db.commit()
            return await orig(token, method, **kw)
        monkeypatch.setattr(wr, "slack_call", accept_meanwhile)
        await wr.process_message(t.id, ev(ch))
        assert queued == [(t.id, "war_room.rerender", case.id, {"reply_ts": "9.9", "channel": ch})]
    run(body)


def test_prune_drops_old_seen_rows_and_expires_suggestions_of_long_closed_cases(slack):
    async def body(ids):
        old = datetime.now(timezone.utc) - timedelta(days=8)
        async with AsyncSessionLocal() as db:
            t, case, ch = await room(db, ids)
            _, open_case, ch2 = await room(db, ids)
            await wr.process_message(t.id, ev(ch))
            db.add(WarRoomMessageSeen(slack_channel=ch, ts="0.000001", tenant_id=t.id, created_at=old))
            db.add(WarRoomSuggestion(tenant_id=open_case.tenant_id, case_id=open_case.id, kind="artifact",
                                     payload={"type": "ip", "value": "1.2.3.4"}, value_key="ip:1.2.3.4", status="pending"))
            await db.commit()
            await db.execute(update(Case).where(Case.id == case.id).values(status="closed", updated_at=old))
            await db.commit()
        await wr.prune()
        assert await seen(ch) == 1   # the fresh marker stays
        assert {r.status for r in await rows(case.id)} == {"dismissed"}
        assert {r.status for r in await rows(open_case.id)} == {"pending"}
    run(body)


def test_slack_button_clicks_use_the_default_queue():
    from app.worker import celery_app
    assert celery_app.conf.task_routes["app.tasks.workflows.handle_slack_interaction_task"] == {"queue": "celery"}
    assert celery_app.conf.beat_schedule["prune-war-room"]["task"] == wr.prune_war_room_task.name


class _Resp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


@pytest.mark.parametrize("second, ok", [(_Resp(200, {"ok": True, "ts": "1"}), True),
                                        (_Resp(429, {"ok": False, "error": "ratelimited"}), False)])
def test_slack_call_honours_retry_after_once(monkeypatch, second, ok):
    from app.services import slack_service as ss
    answers = [_Resp(429, {"ok": False, "error": "ratelimited"}, {"Retry-After": "120"}), second]
    slept, sent = [], []

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            sent.append(url)
            return answers.pop(0)

    async def no_sleep(s):
        slept.append(s)
    monkeypatch.setattr(ss.httpx, "AsyncClient", Client)
    monkeypatch.setattr(ss.asyncio, "sleep", no_sleep)

    async def go():
        if ok:
            assert (await ss.slack_call("x", "chat.postMessage", channel="C1"))["ts"] == "1"
        else:
            with pytest.raises(ss.SlackError) as e:
                await ss.slack_call("x", "chat.postMessage", channel="C1")
            assert e.value.code == "ratelimited"
    import asyncio
    asyncio.run(go())
    assert slept == [ss.MAX_RETRY_AFTER] and len(sent) == 2   # capped wait, exactly one more try


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


@pytest.mark.parametrize("subtype", ["thread_broadcast", "file_share"])
def test_dispatch_forwards_broadcasts_and_messages_with_a_file(delays, subtype):
    slack_bot.dispatch_event(_integ(), _msg(subtype=subtype))
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
    return WarRoomSuggestion(id=id, case_id=42, kind=kind, payload=payload, status=status, resolved_by_user_id=by)


def test_suggestions_reply_blocks():
    s = [_sug(1, "artifact", {"type": "ip", "value": "1.2.3.4"}),
         _sug(2, "artifact", {"type": "hostname", "value": "WS<1>"}),
         _sug(3, "artifact", {"type": "username", "value": "corp\\bob"}),
         _sug(4, "milestone", {"key": "contained", "at": "2026-10-26T14:32:00+01:00"})]
    blocks = suggestions_reply(s, 7)
    text = " ".join(b["text"]["text"] for b in blocks if b["type"] == "section")
    assert "IP `1.2.3.4`" in text and "host `WS&lt;1&gt;`" in text and "user `corp\\bob`" in text
    assert "*Contained* milestone to Mon 26 Oct 14:32" in text
    vals = [e["value"] for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert "wrs:7:1:a" in vals and "wrs:7:1:d" in vals and "wrs:7:4:a" in vals


def test_suggestions_reply_resolved_rows():
    s = [_sug(1, "artifact", {"type": "ip", "value": "1.2.3.4"}, "accepted", 5),
         _sug(2, "artifact", {"type": "ip", "value": "5.6.7.8"}, "dismissed"),
         _sug(3, "milestone", {"key": "contained", "at": "2026-10-26T14:32:00+00:00"}, "conflict")]
    blocks = suggestions_reply(s, 7, names={5: "Ana"})
    ctx = " ".join(e["text"] for b in blocks if b["type"] == "context" for e in b["elements"])
    assert ":white_check_mark: Ana added the IP `1.2.3.4` to the case." in ctx
    assert ":no_entry_sign: Dismissed — it won't be suggested again for this case." in ctx
    assert "*Contained* out of order" in ctx and "Detected" not in ctx
    assert not [b for b in blocks if b["type"] == "actions"]


def _texts(blocks):
    out = []
    for b in blocks:
        if b["type"] == "section":
            out.append(b["text"]["text"])
        elif b["type"] == "context":
            out += [e["text"] for e in b["elements"]]
        elif b["type"] == "actions":
            out += [e["text"]["text"] for e in b["elements"]]
    return " | ".join(out)


def test_reply_header_and_quip_stable():
    from app.services.slack_blocks import SOC_BOT_QUIPS
    s = [_sug(5, "artifact", {"type": "ip", "value": "1.2.3.4"}), _sug(6, "artifact", {"type": "domain", "value": "a.com"})]
    first = suggestions_reply(s, 7)
    assert "SOC-bot spotted something new for case #42" in first[0]["text"]["text"]
    quip = first[1]["elements"][0]["text"]
    assert quip in SOC_BOT_QUIPS
    assert suggestions_reply(s, 7)[1]["elements"][0]["text"] == quip
    s[1].status, s[1].resolved_by_user_id = "dismissed", 1
    assert suggestions_reply(s, 7, names={1: "Ana"})[1]["elements"][0]["text"] == quip


def test_artifact_row_is_a_question():
    text = _texts(suggestions_reply([_sug(1, "artifact", {"type": "ip", "value": "1.2.3.4"}),
                                     _sug(2, "artifact", {"type": "hostname", "value": "WS1"})], 7))
    assert ":globe_with_meridians: Should I add the IP `1.2.3.4` to the case artifacts?" in text
    assert ":desktop_computer: Should I add the host `WS1` to the case artifacts?" in text
    assert "Add to case" in text


def test_milestone_row_wording():
    text = _texts(suggestions_reply([_sug(1, "milestone", {"key": "contained", "at": "2026-10-26T14:32:00+01:00"})], 7))
    assert ":shield: This sounds like the containment time. Should I set the case's *Contained* milestone to" in text
    assert "Set milestone" in text


def test_resolved_lines_wording():
    s = [_sug(1, "milestone", {"key": "recovered", "at": "2026-10-26T14:32:00+00:00"}, "accepted", 5),
         _sug(2, "artifact", {"type": "ip", "value": "5.6.7.8"}, "dismissed", 5),
         _sug(3, "milestone", {"key": "recovered", "at": "2026-10-26T14:32:00+00:00"}, "conflict")]
    text = _texts(suggestions_reply(s, 7, names={5: "Ana"}))
    assert ":white_check_mark: Ana set *Recovered* to" in text
    assert ":no_entry_sign: Dismissed by Ana" in text
    assert "*Recovered* out of order" in text and "Detected" not in text
