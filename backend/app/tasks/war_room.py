"""War-room listening: a Slack message event becomes SOC-bot suggestions and a thread reply."""
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.user import User
from app.models.war_room_suggestion import WarRoomMessageSeen, WarRoomSuggestion
from app.services.slack_blocks import suggestions_reply
from app.services.slack_service import SlackError, load_integration, slack_call
from app.services.war_room_extract import extract_candidates
from app.services.war_room_suggestions import record_candidates
from app.utils.tenant_state import tenant_deactivated
from app.tasks.runner import _run_async
from app.worker import celery_app

logger = logging.getLogger(__name__)
TZ_TTL = 24 * 3600


def _redis():
    import redis.asyncio as aioredis
    return aioredis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)


async def _slack_profile(token: str, tenant_id: int, slack_user: str) -> dict:
    """{"tz","email"} for the author, cached 24h in Redis (best effort)."""
    key = f"slack:tz:{tenant_id}:{slack_user}"
    r = None
    try:
        r = _redis()
        cached = await r.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        logger.debug("tz cache read failed", exc_info=True)
    try:
        u = (await slack_call(token, "users.info", user=slack_user)).get("user") or {}
    except SlackError:
        u = {}
    prof = {"tz": u.get("tz"), "email": (u.get("profile") or {}).get("email")}
    if r is not None and (prof["tz"] or prof["email"]):
        try:
            await r.set(key, json.dumps(prof), ex=TZ_TTL)
        except Exception:
            logger.debug("tz cache write failed", exc_info=True)
    if r is not None:
        try:
            await r.aclose()
        except Exception:
            pass
    return prof


def _zone(*names):
    for n in names:
        if n:
            try:
                return ZoneInfo(n)
            except Exception:
                continue
    return ZoneInfo("UTC")


async def _release(channel: str, ts: str) -> None:
    """Forget the message so a Celery retry can reprocess it."""
    async with AsyncSessionLocal() as db:
        await db.execute(delete(WarRoomMessageSeen).where(
            WarRoomMessageSeen.slack_channel == channel, WarRoomMessageSeen.ts == ts))
        await db.commit()


async def process_message(tenant_id: int, event: dict) -> None:
    channel, ts, user = event["channel"], event["ts"], event.get("user")
    async with AsyncSessionLocal() as db:
        if await tenant_deactivated(db, tenant_id):
            return  # queued before a deactivation: no rows, no suggestions, no Slack call
        db.add(WarRoomMessageSeen(slack_channel=channel, ts=ts, tenant_id=tenant_id))
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()  # redelivery: already handled
            return
        case = (await db.execute(select(Case).where(
            Case.slack_channel_id == channel, Case.tenant_id == tenant_id, Case.slack_listen.is_(True)))).scalars().first()
        if case is None:
            await db.rollback()  # not a listening war room for this tenant: leave no trace
            return
        try:
            integ, token = await load_integration(db, tenant_id)
        except SlackError:
            await db.rollback()
            return
        integ.last_message_event_at = datetime.now(timezone.utc)
        case_id = case.id
        await db.commit()  # no Slack I/O inside an open transaction

    try:
        prof = await _slack_profile(token, tenant_id, user)
        async with AsyncSessionLocal() as db:
            case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().one()
            linked = None
            if prof.get("email"):
                linked = (await db.execute(select(User).where(
                    func.lower(User.email) == prof["email"].lower()))).scalars().first()
            tz = _zone(prof.get("tz"), linked.timezone if linked else None)
            message_at = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            cands = extract_candidates(event.get("text") or "", tz=tz, message_at=message_at,
                                       exclude_emails=frozenset({prof["email"]}) if prof.get("email") else frozenset())
            recorded = await record_candidates(db, case, cands, channel=channel, source_ts=ts, permalink=None)
            await db.commit()
            ids = [s.id for s in recorded]
    except Exception:
        await _release(channel, ts)
        raise
    if not ids:
        return

    thread_ts = event.get("thread_ts") or ts
    try:
        reply_ts = (await slack_call(token, "chat.postMessage", channel=channel, thread_ts=thread_ts,
                                     blocks=suggestions_reply(recorded, tenant_id), text="SOC-bot suggestions")).get("ts")
    except SlackError:
        logger.exception("war room reply failed (tenant %s, channel %s)", tenant_id, channel)
        return  # suggestions are saved; the message stays marked seen so a retry can't double-post
    permalink = None
    try:
        permalink = (await slack_call(token, "chat.getPermalink", channel=channel, message_ts=ts)).get("permalink")
    except SlackError:
        logger.debug("permalink lookup failed", exc_info=True)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(WarRoomSuggestion).where(
            WarRoomSuggestion.id.in_(ids), WarRoomSuggestion.tenant_id == tenant_id))).scalars().all()
        for s in rows:
            s.reply_ts, s.permalink = reply_ts, permalink
        await db.commit()


@celery_app.task(acks_late=True)
def war_room_message_task(tenant_id, event):
    _run_async(process_message(tenant_id, event))
