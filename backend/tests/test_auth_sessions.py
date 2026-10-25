"""Session service: rotating, reuse-detecting refresh tokens. Rows are created via guest_fixtures and
removed by explicit id (auth_sessions also cascade from the user delete in cleanup)."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, update

from app.auth import sessions
from app.auth.sessions import RefreshError
from app.db.session import AsyncSessionLocal
from app.models.auth_session import AuthSession
from app.models.user import User
from tests.guest_fixtures import make_tenant, make_user, run


def _with_session(test, *, kind="web"):
    """Create tenant+user+session, run test(db, row, token, ids, user), always delete the session row by id."""
    async def body(ids):
        sids = []
        try:
            async with AsyncSessionLocal() as db:
                t = await make_tenant(db, ids)
                u = await make_user(db, ids)
                row, token = await sessions.create_session(db, u, kind=kind, amr="pwd", tenant_id=t.id)
                await db.commit()
                sids.append(row.id)
            await test(row.id, token, ids, u, sids)
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(AuthSession).where(AuthSession.id.in_(sids or ["-"])))
                await db.commit()
    run(body)


async def _row(sid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(AuthSession).where(AuthSession.id == sid))).scalar_one()


async def _age_rotation(sid, seconds):
    async with AsyncSessionLocal() as db:
        await db.execute(update(AuthSession).where(AuthSession.id == sid).values(
            rotated_at=datetime.now(timezone.utc) - timedelta(seconds=seconds)))
        await db.commit()


def test_create_validates_kind_and_amr():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            u = await make_user(db, ids)
            with pytest.raises(ValueError):
                await sessions.create_session(db, u, kind="api", amr="pwd")
            with pytest.raises(ValueError):
                await sessions.create_session(db, u, kind="web", amr="link")
            await db.rollback()
    run(body)


def test_rotate_issues_new_secret():
    async def t(sid, token, ids, u, sids):
        assert token.startswith(sid + ".")
        before = await _row(sid)
        assert before.refresh_hash == sessions.hash_secret(token.split(".", 1)[1])
        assert before.tv == u.token_version
        async with AsyncSessionLocal() as db:
            row, user, new = await sessions.rotate(db, token, kind="web")
            await db.commit()
        assert row.id == sid and user.id == u.id and new != token and new.startswith(sid + ".")
        after = await _row(sid)
        assert after.refresh_hash == sessions.hash_secret(new.split(".", 1)[1])
        assert after.prev_refresh_hash == before.refresh_hash
        assert after.rotated_at is not None and after.revoked_at is None
        # The new secret works in turn.
        async with AsyncSessionLocal() as db:
            await sessions.rotate(db, new, kind="web")
            await db.commit()
    _with_session(t)


def test_old_secret_reuse_revokes(monkeypatch):
    events = []

    async def fake_audit(category, action, **kw):
        events.append((category, action, kw))
    monkeypatch.setattr(sessions, "audit_event", fake_audit)

    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            await sessions.rotate(db, token, kind="web")
            await db.commit()
        await _age_rotation(sid, sessions.REFRESH_GRACE_SECONDS + 5)
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, token, kind="web")
            await db.rollback()  # the revocation must survive a caller rollback
        assert ei.value.code == "reuse"
        assert (await _row(sid)).revoked_at is not None
        assert events and events[0][:2] == ("security", "session.refresh_reuse")
        assert events[0][2]["user_id"] == u.id and events[0][2]["changes"] == {"session_id": sid}
    _with_session(t)


def test_unknown_secret_revokes():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, f"{sid}.forged", kind="web")
        assert ei.value.code == "reuse"
        assert (await _row(sid)).revoked_at is not None
    _with_session(t)


def test_concurrent_refresh_grace():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            _, _, first = await sessions.rotate(db, token, kind="web")
            await db.commit()
        rotated = await _row(sid)
        async with AsyncSessionLocal() as db:
            row, user, second = await sessions.rotate(db, token, kind="web")  # old secret, within grace
            await db.commit()
        assert second is None and row.id == sid and user.id == u.id
        after = await _row(sid)
        assert after.revoked_at is None
        assert after.refresh_hash == rotated.refresh_hash == sessions.hash_secret(first.split(".", 1)[1])
        assert after.prev_refresh_hash == rotated.prev_refresh_hash
        assert after.rotated_at == rotated.rotated_at  # grace window is not extended by replays
        # The winner's secret still rotates normally afterwards, and so does its successor.
        async with AsyncSessionLocal() as db:
            _, _, third = await sessions.rotate(db, first, kind="web")
            await db.commit()
        assert third is not None
        async with AsyncSessionLocal() as db:
            _, _, fourth = await sessions.rotate(db, third, kind="web")
            await db.commit()
        assert fourth is not None and (await _row(sid)).revoked_at is None
    _with_session(t)


def test_parallel_rotations_serialize():
    """Two simultaneous refreshes with the same secret: the row lock serializes them, the second lands in
    the grace window, and the session survives."""
    import asyncio

    async def t(sid, token, ids, u, sids):
        async def one():
            async with AsyncSessionLocal() as db:
                _, _, new = await sessions.rotate(db, token, kind="web")
                await db.commit()
                return new
        results = await asyncio.gather(one(), one())
        winners = [r for r in results if r is not None]
        assert len(winners) == 1 and results.count(None) == 1
        after = await _row(sid)
        assert after.revoked_at is None
        assert after.refresh_hash == sessions.hash_secret(winners[0].split(".", 1)[1])
    _with_session(t)


def test_inactive_user_refused():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == u.id).values(is_active=False))
            await db.commit()
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, token, kind="web")
            await db.rollback()
        assert ei.value.code == "revoked"
        assert (await _row(sid)).revoked_at is not None
    _with_session(t)


def test_extra_dot_token_is_reuse():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, f"{sid}.a.b", kind="web")
        assert ei.value.code == "reuse"
        assert (await _row(sid)).revoked_at is not None
    _with_session(t)


def test_idle_expiry():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            await db.execute(update(AuthSession).where(AuthSession.id == sid).values(
                last_used_at=datetime.now(timezone.utc) - timedelta(days=31)))
            await db.commit()
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, token, kind="web")
        assert ei.value.code == "expired"
    _with_session(t)


def test_idle_expired_stale_secret_reports_expired_not_reuse():
    """A stale token for an idle-expired session is just expired: no reuse alarm, no revocation."""
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            _row2, _u, newer = await sessions.rotate(db, token, kind="web")
            await db.commit()
        await _age_rotation(sid, 3600)  # outside the grace window
        async with AsyncSessionLocal() as db:
            await db.execute(update(AuthSession).where(AuthSession.id == sid).values(
                last_used_at=datetime.now(timezone.utc) - timedelta(days=31)))
            await db.commit()
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, token, kind="web")  # the pre-rotation (stale) secret
        assert ei.value.code == "expired"
        assert (await _row(sid)).revoked_at is None
    _with_session(t)


def test_tv_bump_refused():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == u.id).values(token_version=User.token_version + 1))
            await db.commit()
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, token, kind="web")
            await db.rollback()
        assert ei.value.code == "revoked"
        assert (await _row(sid)).revoked_at is not None
    _with_session(t)


def test_revoked_session_refused():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            assert await sessions.revoke(db, sid, user_id=u.id) is True
            await db.commit()
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, token, kind="web")
        assert ei.value.code == "revoked"
    _with_session(t)


def test_kind_mismatch():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, token, kind="oauth")
        assert ei.value.code == "invalid"
        assert (await _row(sid)).revoked_at is None
    _with_session(t)


@pytest.mark.parametrize("bad", ["", "nodot", ".x", "abc.", "a" * 32 + ".secret"])
def test_malformed_tokens_invalid(bad):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            with pytest.raises(RefreshError) as ei:
                await sessions.rotate(db, bad, kind="web")
        assert ei.value.code == "invalid"
    run(body)


def test_revoke_scoping():
    async def t(sid, token, ids, u, sids):
        async with AsyncSessionLocal() as db:
            other = await make_user(db, ids)
            assert await sessions.revoke(db, sid, user_id=other.id) is False
            # A web session is never revocable through the tenant-admin (oauth) scope.
            assert await sessions.revoke(db, sid, tenant_id=(await _row(sid)).tenant_id) is False
            await db.commit()
        assert (await _row(sid)).revoked_at is None
        async with AsyncSessionLocal() as db:
            assert await sessions.revoke(db, sid, user_id=u.id) is True
            assert await sessions.revoke(db, sid, user_id=u.id) is False  # already revoked
            await db.commit()
    _with_session(t)


def test_revoke_tenant_scope_oauth():
    async def t(sid, token, ids, u, sids):
        tid = (await _row(sid)).tenant_id
        async with AsyncSessionLocal() as db:
            assert await sessions.revoke(db, sid, tenant_id=tid + 100000) is False
            assert await sessions.revoke(db, sid, tenant_id=tid) is True
            await db.commit()
    _with_session(t, kind="oauth")


def test_is_live_bumps_at_most_once_per_minute():
    async def t(sid, token, ids, u, sids):
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        async with AsyncSessionLocal() as db:
            await db.execute(update(AuthSession).where(AuthSession.id == sid).values(last_used_at=old))
            await db.commit()
        async with AsyncSessionLocal() as db:
            row = await sessions.is_live(db, sid)
            assert row is not None
            await db.commit()
        bumped = (await _row(sid)).last_used_at
        assert bumped > old + timedelta(minutes=4)
        async with AsyncSessionLocal() as db:
            await sessions.is_live(db, sid)
            await db.commit()
        assert (await _row(sid)).last_used_at == bumped  # within a minute: no write
        async with AsyncSessionLocal() as db:
            assert await sessions.is_live(db, "f" * 32) is None
            await sessions.revoke(db, sid, user_id=u.id)
            await db.commit()
        async with AsyncSessionLocal() as db:
            assert await sessions.is_live(db, sid) is None
    _with_session(t)
