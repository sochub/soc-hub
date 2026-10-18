"""Guest digests: queue rows in the caller's transaction; after commit, schedule one delayed task per guest."""
import logging
from typing import Optional

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.case_guest import CaseGuest, GuestNotification

logger = logging.getLogger(__name__)
DIGEST_DELAY_SECONDS = 300  # ponytail: fixed window; make it a tenant setting only if someone asks
_KEY = "guest_digest:{}"
_STASH = "guest_digest"


_client = None


def _redis():
    """One lazy client per process; short timeouts so a Redis outage can't stall a request's commit hook."""
    global _client
    if _client is None:
        import redis
        _client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    return _client


def _dispatch(gid: int) -> None:
    from app.tasks.guest_digest import send_guest_digest_task  # late import: worker import cycle
    send_guest_digest_task.apply_async(args=[gid], countdown=DIGEST_DELAY_SECONDS)


def _schedule(gid: int) -> None:
    # NX: at most one pending digest per guest; EX is a safety net if the task never runs.
    key = _KEY.format(gid)
    if _redis().set(key, 1, nx=True, ex=DIGEST_DELAY_SECONDS * 3):
        try:
            _dispatch(gid)
        except Exception:
            try:
                _redis().delete(key)  # nothing is queued: let the next event re-arm instead of waiting out EX
            except Exception:
                pass  # Redis is down too; EX still expires the key, and the dispatch error is the one worth logging
            raise


def clear_pending(gid: int) -> None:
    _redis().delete(_KEY.format(gid))


async def enqueue(db, case, kind: str, summary: str, exclude_grant_id: Optional[int] = None) -> None:
    gids = (await db.execute(select(CaseGuest.id).where(
        CaseGuest.case_id == case.id, CaseGuest.revoked_at.is_(None), CaseGuest.expires_at > func.now()))
    ).scalars().all()
    gids = [g for g in gids if g != exclude_grant_id]
    for gid in gids:
        db.add(GuestNotification(case_guest_id=gid, case_id=case.id, kind=kind, summary=summary[:200]))
    if gids:
        db.info.setdefault(_STASH, set()).update(gids)


def _after_commit(session) -> None:
    if session.in_nested_transaction():
        return
    for gid in session.info.pop(_STASH, ()):
        try:
            _schedule(gid)
        except Exception as e:  # the commit stands; the rows are picked up by the next digest
            logger.warning("guest digest schedule failed id=%s err=%s", gid, type(e).__name__)


def _after_soft_rollback(session, previous_transaction) -> None:
    if not previous_transaction.nested and previous_transaction.parent is None:
        session.info.pop(_STASH, None)


def register_guest_digest_hook() -> None:
    if not event.contains(Session, "after_commit", _after_commit):
        event.listen(Session, "after_commit", _after_commit)
        event.listen(Session, "after_soft_rollback", _after_soft_rollback)
