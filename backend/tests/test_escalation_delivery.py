import pytest
from sqlalchemy import select

from app.escalations import events
from app.models.escalation import CaseEscalation
from app.models.notification import Notification
from app.notifications import delivery
from app.services.slack_service import SlackError
from tests.test_notification_delivery import FakeRedis, FakeSlack, run, wire
from tests.test_notification_triggers import case_of


def _tier(**kw):
    base = {"id": 1, "position": 2, "name": "Tier 2", "accept_timeout_minutes": 30, "notify_email": False,
            "notify_slack": False, "slack_channel": None, "groups": []}
    return {**base, **kw}


async def make_esc(db, ctx, type_="escalated", status="pending", **tier_kw):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    tiers = [_tier(id=9, position=1, name="Tier 1"), _tier(**tier_kw)]
    row = CaseEscalation(
        tenant_id=ctx["t"], case_id=case.id, to_tier_id=None, requested_by=i["A"], reason="r", summary="Seen on 3 hosts",
        requested_help="h", status=status,
        policy_snapshot={"tiers": tiers, "to": {"position": 2, "tier_name": "Tier 2", "group_id": None, "group_name": None},
                         "from": {"position": 1}})
    db.add(row)
    await db.flush()
    n = Notification(tenant_id=ctx["t"], user_id=i["C"], case_id=case.id, type=type_, actor_id=i["A"],
                     summary="Case escalated", escalation_id=row.id)
    db.add(n)
    await db.commit()
    return row, n


@run
async def test_both_flags_send_both(db, ctx, mp):
    _, n = await make_esc(db, ctx, notify_slack=True, notify_email=True)
    slack, sent = FakeSlack(), []
    wire(mp, slack=slack, smtp=True, sent=sent)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "slack+email"
    assert [c[0] for c in slack.calls].count("chat.postMessage") == 1 and len(sent) == 1


@run
async def test_email_only_no_slack(db, ctx, mp):
    _, n = await make_esc(db, ctx, notify_email=True)
    slack, sent = FakeSlack(), []
    wire(mp, slack=slack, smtp=True, sent=sent)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "email"
    assert slack.calls == [] and len(sent) == 1


@run
async def test_slack_only_no_email(db, ctx, mp):
    _, n = await make_esc(db, ctx, notify_slack=True)
    slack, sent = FakeSlack(), []
    wire(mp, slack=slack, smtp=True, sent=sent)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "slack"
    assert sent == []


@run
async def test_no_flags_skipped(db, ctx, mp):
    _, n = await make_esc(db, ctx)
    slack, sent = FakeSlack(), []
    wire(mp, slack=slack, smtp=True, sent=sent)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "skipped"
    assert slack.calls == [] and sent == []


@run
async def test_escalation_notifications_bypass_rate_limit(db, ctx, mp):
    _, n1 = await make_esc(db, ctx, notify_email=True)
    _, n2 = await make_esc(db, ctx, type_="escalation_overdue", status="accepted", notify_email=True)
    sent, r = [], FakeRedis()
    wire(mp, slack=None, smtp=True, sent=sent)
    assert await delivery.deliver_one(db, n1.id, r) == "email"
    assert await delivery.deliver_one(db, n2.id, r) == "email"
    assert len(sent) == 2 and r.keys == set()


@run
async def test_plain_notification_still_rate_limited(db, ctx, mp):
    _, n = await make_esc(db, ctx, notify_email=True)
    n.type, n.escalation_id = "assigned", None
    await db.commit()
    wire(mp, slack=None, smtp=True, sent=[])
    r = FakeRedis()
    assert await delivery.deliver_one(db, n.id, r) == "email"
    assert await delivery.deliver_one(db, n.id, r) == "rate_limited"


@run
async def test_channel_replaces_dms_email_stays(db, ctx, mp):
    row, n = await make_esc(db, ctx, notify_slack=True, notify_email=True, slack_channel="#soc-t2")
    slack, sent = FakeSlack(), []
    wire(mp, slack=slack, smtp=True, sent=sent)
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "email"
    assert slack.calls == [] and len(sent) == 1
    assert await delivery.post_escalation_channel(db, row.id) == "posted"
    assert [c[0] for c in slack.calls] == ["chat.postMessage"]
    assert slack.calls[0][1]["channel"] == "#soc-t2" and slack.calls[0][1]["blocks"]


@run
async def test_channel_post_failure_is_logged_not_raised(db, ctx, mp):
    row, _ = await make_esc(db, ctx, notify_slack=True, slack_channel="#soc-t2")
    wire(mp, slack=FakeSlack("chat.postMessage", SlackError("chat.postMessage: channel_not_found", code="channel_not_found")))
    assert await delivery.post_escalation_channel(db, row.id) == "failed"


@run
async def test_channel_post_skips_closed_row(db, ctx, mp):
    row, _ = await make_esc(db, ctx, status="withdrawn", notify_slack=True, slack_channel="#soc-t2")
    slack = FakeSlack()
    wire(mp, slack=slack)
    assert await delivery.post_escalation_channel(db, row.id) == "skipped"
    assert slack.calls == []


@run
async def test_announce_enqueues_channel_post_after_commit(db, ctx, mp):
    from app.escalations import service as esc
    events.register_after_commit_hook()
    posted = []
    mp.setattr(events, "_enqueue_channel", lambda rid: posted.append(rid))
    row, _ = await make_esc(db, ctx, notify_slack=True, slack_channel="#soc-t2")
    case = await case_of(db, ctx)
    await esc._announce(db, case, row, ctx["ids"]["A"], "escalated", "x")
    assert posted == []  # not before the commit
    await db.commit()
    assert posted == [row.id]
    row2, _ = await make_esc(db, ctx, status="closed")  # no channel on this tier
    await esc._announce(db, case, row2, ctx["ids"]["A"], "escalated", "x")
    await db.commit()
    assert posted == [row.id]
    row.status = "accepted"  # one pending row per case
    await db.commit()
    row3, _ = await make_esc(db, ctx, slack_channel="#soc-t2")  # channel kept, Slack unchecked: no post
    await esc._announce(db, case, row3, ctx["ids"]["A"], "escalated", "x")
    await db.commit()
    assert posted == [row.id]


@run
async def test_channel_not_posted_when_slack_unchecked(db, ctx, mp):
    row, n = await make_esc(db, ctx, notify_email=True, slack_channel="#soc-t2")
    slack, sent = FakeSlack(), []
    wire(mp, slack=slack, smtp=True, sent=sent)
    assert await delivery.post_escalation_channel(db, row.id) == "skipped"
    assert await delivery.deliver_one(db, n.id, FakeRedis()) == "email"
    assert slack.calls == [] and len(sent) == 1


@run
async def test_channel_enqueue_dropped_on_rollback(db, ctx, mp):
    events.register_after_commit_hook()
    posted = []
    mp.setattr(events, "_enqueue_channel", lambda rid: posted.append(rid))
    await db.execute(select(1))  # open a transaction, as a real request has
    events.stash_channel(db, 123)
    await db.rollback()
    await db.commit()
    assert posted == []
