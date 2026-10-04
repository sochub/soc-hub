import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from app.models.membership import TenantMembership
from app.models.user import User

from app.models.case import Case
from app.models.notification import Notification
from app.notifications import delivery, service
from app.services import case_service
from app.services.slack_service import SlackError
from app.tasks import notifications as tasks
from app.tasks.notifications import deliver_notifications_task
from tests.test_notification_triggers import SENTINEL, case_of, scenario


_REAL_ENQUEUE = delivery._enqueue  # conftest stubs it for every test; hook tests restore it


class FakeRedis:
    def __init__(self):
        self.keys = set()

    async def set(self, key, val, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True

    async def delete(self, key):
        self.keys.discard(key)


class FakeSlack:
    def __init__(self, fail_on=None, exc=None):
        self.calls, self.fail_on, self.exc = [], fail_on, exc

    async def __call__(self, token, method, **args):
        self.calls.append((method, args))
        if method == self.fail_on:
            raise self.exc
        return {"users.lookupByEmail": {"user": {"id": "U1"}},
                "conversations.open": {"channel": {"id": "D1"}}}.get(method, {"ok": True})


def wire(monkeypatch, slack=None, smtp=False, sent=None):
    async def li(db, tenant_id):
        if slack is None:
            raise SlackError("Slack is not configured for this tenant")
        return object(), "xoxb-test"
    monkeypatch.setattr(delivery, "load_integration", li)
    monkeypatch.setattr(delivery, "slack_call", slack or FakeSlack())
    monkeypatch.setattr(delivery, "_smtp_configured", lambda: smtp)
    monkeypatch.setattr(delivery, "_send_email", lambda to, subj, body, **kw: (sent.append((to, subj, body)) if sent is not None else None) or True)


async def make_mention(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    case.title = "Evil <b>&title"
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"{SENTINEL} @[x](user:{i['C']})")
    await db.commit()
    return (await db.execute(select(Notification).where(Notification.user_id == i["C"], Notification.type == "mention"))).scalars().one()


def run(fn):
    """Adapt a (db, ctx, monkeypatch) test into the scenario helper."""
    async def inner(db, ctx):
        mp = pytest.MonkeyPatch()
        try:
            await fn(db, ctx, mp)
        finally:
            mp.undo()
    inner.__name__ = fn.__name__
    return scenario(inner)


@run
async def test_slack_first(db, ctx, mp):
    n = await make_mention(db, ctx)
    slack = FakeSlack()
    wire(mp, slack=slack)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "slack"
    assert [c[0] for c in slack.calls] == ["users.lookupByEmail", "conversations.open", "chat.postMessage"]
    text = slack.calls[2][1]["text"]
    assert n.summary in text and "Evil &lt;b&gt;&amp;title" in text and f"/cases/{ctx['case']}" in text
    assert SENTINEL not in text


@run
async def test_email_fallback_on_users_not_found(db, ctx, mp):
    n = await make_mention(db, ctx)
    sent = []
    wire(mp, slack=FakeSlack("users.lookupByEmail", SlackError("users.lookupByEmail: users_not_found", code="users_not_found")), smtp=True, sent=sent)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "email"
    assert len(sent) == 1 and SENTINEL not in sent[0][2] and "Evil &lt;b&gt;&amp;title" in sent[0][2]


@run
async def test_no_slack_email(db, ctx, mp):
    n = await make_mention(db, ctx)
    wire(mp, slack=None, smtp=True, sent=[])
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "email"


@run
async def test_neither(db, ctx, mp):
    n = await make_mention(db, ctx)
    wire(mp, slack=None, smtp=False)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "none"


@run
async def test_rate_limited(db, ctx, mp):
    n = await make_mention(db, ctx)
    slack = FakeSlack()
    wire(mp, slack=slack)
    r = FakeRedis()
    assert await delivery.deliver_one(db, n.id, r) == "slack"
    assert await delivery.deliver_one(db, n.id, r) == "rate_limited"
    assert [c[0] for c in slack.calls].count("chat.postMessage") == 1


@run
async def test_transient_retries(db, ctx, mp):
    n = await make_mention(db, ctx)
    for exc in (SlackError("chat.postMessage: ratelimited", code="ratelimited"), SlackError("chat.postMessage: [Errno 8] boom")):
        wire(mp, slack=FakeSlack("chat.postMessage", exc))
        r = FakeRedis()
        with pytest.raises(delivery.TransientDeliveryError):
            await delivery.deliver_one(db, n.id, r)
        assert r.keys == set()  # retry must not be rate limited by its own first attempt


@run
async def test_non_external_type_skipped(db, ctx, mp):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await service.follow(db, case, i["B"])
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content="plain")
    await db.commit()
    n = (await db.execute(select(Notification).where(Notification.user_id == i["B"], Notification.type == "comment"))).scalars().one()
    wire(mp, slack=FakeSlack())
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "skipped"


class FakeSMTP:
    fail = False

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise OSError("smtp down for " + msg["To"])


@run
async def test_logs_have_no_pii(db, ctx, mp):
    import smtplib
    from app.services import email_service
    n = await make_mention(db, ctx)
    email = (await db.execute(select(User.email).where(User.id == n.user_id))).scalar_one()
    mp.setattr(smtplib, "SMTP", FakeSMTP)
    mp.setattr(email_service.settings, "SMTP_HOST", "smtp.invalid", raising=False)
    mp.setattr(email_service.settings, "SMTP_FROM_EMAIL", "from@example.test", raising=False)
    mp.setattr(email_service.settings, "SMTP_USER", "", raising=False)
    records = []
    h = logging.Handler()
    h.emit = records.append
    root = logging.getLogger()
    root.addHandler(h)
    old = root.level
    root.setLevel(logging.DEBUG)
    try:
        for fail in (False, True):
            FakeSMTP.fail = fail
            mp.setattr(delivery, "load_integration", lambda db, t: (_ for _ in ()).throw(SlackError("Slack is not configured")))
            mp.setattr(delivery, "_smtp_configured", lambda: True)
            mp.setattr(delivery, "_send_email", email_service._send_email)
            assert await delivery.deliver_one(db, n.id, FakeRedis()) == ("none" if fail else "email")
        wire(mp, slack=FakeSlack("chat.postMessage", SlackError("chat.postMessage: ratelimited", code="ratelimited")))
        with pytest.raises(delivery.TransientDeliveryError):
            await delivery.deliver_one(db, n.id, FakeRedis())
        await tasks._deliver([n.id, 10 ** 9])
    finally:
        FakeSMTP.fail = False
        root.removeHandler(h)
        root.setLevel(old)
    assert any("email sent" in r.getMessage() for r in records) and any("email failed" in r.getMessage() for r in records)
    for r in records:
        m = r.getMessage()
        assert r.exc_info is None or "email" not in m
        assert email not in m and "@example.test" not in m and n.summary not in m and SENTINEL not in m


@run
async def test_removed_member_skipped(db, ctx, mp):
    n = await make_mention(db, ctx)
    await db.execute(delete(TenantMembership).where(TenantMembership.user_id == n.user_id, TenantMembership.tenant_id == n.tenant_id))
    await db.commit()
    slack = FakeSlack()
    wire(mp, slack=slack)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "skipped"
    assert slack.calls == []


@run
async def test_unexpected_error_releases_rate_key(db, ctx, mp):
    n = await make_mention(db, ctx)
    wire(mp, slack=FakeSlack("conversations.open", RuntimeError("boom")))
    r = FakeRedis()
    with pytest.raises(RuntimeError):
        await delivery.deliver_one(db, n.id, r)
    assert r.keys == set()


@run
async def test_rollback_enqueues_nothing(db, ctx, mp):
    i = ctx["ids"]
    calls = []
    mp.setattr(delivery, "_enqueue", _REAL_ENQUEUE)
    mp.setattr(deliver_notifications_task, "delay", lambda ids: calls.append(list(ids)))
    case = await case_of(db, ctx)
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"x @[x](user:{i['C']})")
    assert db.info.get("notif_external")
    await db.rollback()
    assert calls == [] and "notif_external" not in db.info
    case = await case_of(db, ctx)
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"y @[x](user:{i['C']})")
    await db.commit()
    nid = (await db.execute(select(Notification.id).where(Notification.user_id == i["C"], Notification.type == "mention"))).scalars().one()
    assert calls == [[nid]]


@run
async def test_failed_savepoint_keeps_earlier_stash(db, ctx, mp):
    i = ctx["ids"]
    calls = []
    mp.setattr(delivery, "_enqueue", _REAL_ENQUEUE)
    mp.setattr(deliver_notifications_task, "delay", lambda ids: calls.append(list(ids)))
    case = await case_of(db, ctx)
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"x @[x](user:{i['C']})")
    first = list(db.info["notif_external"])
    assert len(first) == 1

    async def boom(*a, **k):
        raise RuntimeError("boom")
    mp.setattr(service, "_actor_name", boom)
    await service.on_case_update(db, case, {"owner_id": {"from": None, "to": i["B"]}}, i["A"])  # SAVEPOINT rolls back, swallowed
    assert db.info["notif_external"] == first
    await db.commit()
    assert calls == [first]


@run
async def test_savepoint_release_does_not_enqueue(db, ctx, mp):
    i = ctx["ids"]
    calls = []
    mp.setattr(delivery, "_enqueue", _REAL_ENQUEUE)
    mp.setattr(deliver_notifications_task, "delay", lambda ids: calls.append(list(ids)))
    case = await case_of(db, ctx)
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"x @[x](user:{i['C']})")
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"y @[x](user:{i['B']})")
    assert calls == []  # two released savepoints, outer transaction still open
    assert len(db.info["notif_external"]) == 2
    await db.commit()
    assert len(calls) == 1 and sorted(calls[0]) == sorted(
        (await db.execute(select(Notification.id).where(Notification.type == "mention", Notification.case_id == ctx["case"]))).scalars().all())


@scenario
async def test_prune(db, ctx):
    i = ctx["ids"]
    now = datetime.now(timezone.utc)

    def mk(created, read=None):
        return Notification(tenant_id=ctx["t"], user_id=i["B"], case_id=ctx["case"], type="comment", summary="s",
                            created_at=now - timedelta(days=created), read_at=(now - timedelta(days=created - 1)) if read else None)
    old_read, old_unread, fresh = mk(91, True), mk(181), mk(1)
    db.add_all([old_read, old_unread, fresh])
    await db.commit()
    ids = [old_read.id, old_unread.id, fresh.id]
    fresh_id = fresh.id
    await tasks._prune(only_ids=ids)
    left = (await db.execute(select(Notification.id).where(Notification.id.in_(ids)))).scalars().all()
    assert left == [fresh_id]
