from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.guests import notify as gnotify
from app.models.case_guest import CaseGuest, GuestNotification
from app.services import email_service
from app.services.case_service import apply_case_update
from app.models.case import Case, CaseStatus
from app.tasks import guest_digest
from tests.guest_fixtures import make_case, make_grant, make_tenant, run


def test_three_events_one_email_and_revoked_skipped(monkeypatch):
    dispatched, sent = [], []
    monkeypatch.setattr(gnotify, "_dispatch", lambda gid: dispatched.append(gid))
    monkeypatch.setattr(email_service, "_smtp_configured", lambda: True)
    monkeypatch.setattr(email_service, "_send_email",
                        lambda to, s, b, log_recipient=True: sent.append((to, b)) or True)

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t, title="Digest case")
            a = await make_grant(db, c, ids.email("a"))
            b = await make_grant(db, c, ids.email("b"))
            await make_grant(db, c, ids.email("gone"), revoked=True)
        for i in range(2):
            async with AsyncSessionLocal() as db:
                case = await db.get(Case, c.id)
                await gnotify.enqueue(db, case, "shared_entry", f"New update shared on case #{c.id}")
                await db.commit()
        async with AsyncSessionLocal() as db:
            case = await db.get(Case, c.id)
            await apply_case_update(db, case=case, update_data={"status": CaseStatus.IN_PROGRESS}, user_id=None)
            await db.commit()
        assert sorted(dispatched) == sorted([a.id, b.id]), dispatched  # one pending task per guest

        async with AsyncSessionLocal() as db:   # revoke b before its digest fires
            row = await db.get(CaseGuest, b.id)
            row.revoked_at = datetime.now(timezone.utc)
            await db.commit()
        assert await guest_digest.send_digest(a.id) is True
        assert await guest_digest.send_digest(b.id) is False
        assert len(sent) == 1 and sent[0][0] == a.email
        assert sent[0][1].count("<li>") == 3 and "In Progress" in sent[0][1]
        async with AsyncSessionLocal() as db:
            unsent = (await db.execute(select(GuestNotification).where(
                GuestNotification.case_id == c.id, GuestNotification.sent_at.is_(None)))).scalars().all()
            assert unsent == []
        # after the digest, the next event schedules a new one
        async with AsyncSessionLocal() as db:
            case = await db.get(Case, c.id)
            await gnotify.enqueue(db, case, "shared_entry", "x")
            await db.commit()
        assert dispatched.count(a.id) == 2
    run(body)


def test_rollback_drops_stash(monkeypatch):
    dispatched = []
    monkeypatch.setattr(gnotify, "_dispatch", lambda gid: dispatched.append(gid))

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            await make_grant(db, c, ids.email("r"))
        async with AsyncSessionLocal() as db:
            case = await db.get(Case, c.id)
            await gnotify.enqueue(db, case, "shared_entry", "x")
            await db.rollback()
            assert gnotify._STASH not in db.info
            case = await db.get(Case, c.id)
            case.guest_summary = "x"
            await db.commit()
        assert dispatched == []
    run(body)


def test_failed_send_rearms_digest(monkeypatch):
    dispatched = []
    monkeypatch.setattr(gnotify, "_dispatch", lambda gid: dispatched.append(gid))
    monkeypatch.setattr(email_service, "_smtp_configured", lambda: True)
    monkeypatch.setattr(email_service, "_send_email", lambda to, s, b, log_recipient=True: False)

    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            a = await make_grant(db, c, ids.email("f"))
        async with AsyncSessionLocal() as db:
            case = await db.get(Case, c.id)
            await gnotify.enqueue(db, case, "shared_entry", "x")
            await db.commit()
        assert dispatched == [a.id]
        assert await guest_digest.send_digest(a.id) is False
        assert dispatched == [a.id, a.id]
        async with AsyncSessionLocal() as db:
            unsent = (await db.execute(select(GuestNotification).where(
                GuestNotification.case_id == c.id, GuestNotification.sent_at.is_(None)))).scalars().all()
            assert len(unsent) == 1
    run(body)


def test_schedule_rearms_when_dispatch_fails(monkeypatch):
    import pytest

    def boom(gid):
        raise RuntimeError("broker down")
    monkeypatch.setattr(gnotify, "_dispatch", boom)
    with pytest.raises(RuntimeError):
        gnotify._schedule(424242)
    calls = []
    monkeypatch.setattr(gnotify, "_dispatch", lambda gid: calls.append(gid))
    gnotify._schedule(424242)  # the NX key was released, so the next event schedules again
    assert calls == [424242]
