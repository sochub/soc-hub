"""War-room listening: a Slack message event becomes SOC-bot suggestions and a thread reply."""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.case import Case, CaseStatus
from app.models.membership import TenantMembership
from app.models.user import User
from app.models.war_room_suggestion import WarRoomMessageSeen, WarRoomSuggestion
from app.services.slack_blocks import suggestions_reply
from app.services.slack_service import SlackError, load_integration, slack_call
from app.services.war_room_events import queue_card
from app.services.war_room_extract import extract_candidates
from app.services.war_room_suggestions import declared_milestones, record_candidates, split_for_reply
from app.utils.tenant_state import tenant_deactivated
from app.tasks.runner import _run_async
from app.worker import celery_app

logger = logging.getLogger(__name__)
TZ_TTL = 24 * 3600
KEEP_DAYS = 7
# Slack errors worth another try; anything else (channel archived, bot removed, bad token) will not heal by itself
_TRANSIENT = {None, "ratelimited", "internal_error", "service_unavailable", "request_timeout", "fatal_error"}


def _redis():
    import redis.asyncio as aioredis
    return aioredis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)


async def _slack_profile(token: str, tenant_id: int, slack_user: str) -> dict:
    """{"tz","email","name"} for the author, cached 24h in Redis (best effort)."""
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
    prof = {"tz": u.get("tz"), "email": (u.get("profile") or {}).get("email"),
            "name": u.get("real_name") or (u.get("profile") or {}).get("real_name")}
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
    """The first usable timezone name, or None: an unknown timezone is never read as UTC."""
    for n in names:
        if n:
            try:
                return ZoneInfo(n)
            except Exception:
                continue
    return None


async def _linked_timezones(db, tenant_id: int, slack_user: str, email) -> list:
    """Timezones of the SOC Hub user behind a Slack author: the member bound to that Slack account, then the
    account with the Slack profile's email."""
    out = [(await db.execute(select(User.timezone).join(TenantMembership, TenantMembership.user_id == User.id).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.slack_user_id == slack_user))).scalars().first()]
    if email:
        out.append((await db.execute(select(User.timezone).where(
            func.lower(User.email) == email.lower()))).scalars().first())
    return out


async def process_message(tenant_id: int, event: dict) -> None:
    """Safe to run again for the same message: the "seen" row and the suggestions commit together, and a run
    that finds the message already seen only posts the reply if none was posted yet."""
    channel, ts, user = event["channel"], event["ts"], event.get("user")
    async with AsyncSessionLocal() as db:
        if await tenant_deactivated(db, tenant_id):
            return  # queued before a deactivation: no rows, no suggestions, no Slack call
        case = (await db.execute(select(Case).where(
            Case.slack_channel_id == channel, Case.tenant_id == tenant_id, Case.slack_listen.is_(True)))).scalars().first()
        # not a listening war room for this tenant, or the incident is over: leave no trace
        if case is None or case.status in (CaseStatus.RESOLVED, CaseStatus.CLOSED):
            return
        try:
            _, token = await load_integration(db, tenant_id)
        except SlackError:
            return
        case_id = case.id
        seen = await db.get(WarRoomMessageSeen, (channel, ts)) is not None

    if not seen:
        # every Slack lookup happens before the write transaction opens
        prof = await _slack_profile(token, tenant_id, user)
        tz = _zone(prof.get("tz"))
        if tz is None:  # users.info failed or has no timezone: use the SOC Hub user behind the author, if any
            async with AsyncSessionLocal() as db:
                tz = _zone(*await _linked_timezones(db, tenant_id, user, prof.get("email")))
        cands = extract_candidates(event.get("text") or "", tz=tz,
                                   message_at=datetime.fromtimestamp(float(ts), tz=timezone.utc),
                                   exclude_emails=frozenset({prof["email"]}) if prof.get("email") else frozenset())
        permalink = None
        if cands:
            try:
                permalink = (await slack_call(token, "chat.getPermalink", channel=channel, message_ts=ts)).get("permalink")
            except SlackError:
                logger.debug("permalink lookup failed", exc_info=True)
        async with AsyncSessionLocal() as db:
            db.add(WarRoomMessageSeen(slack_channel=channel, ts=ts, tenant_id=tenant_id))
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()  # a concurrent delivery of the same message owns it
                return
            integ, _ = await load_integration(db, tenant_id)
            integ.last_message_event_at = datetime.now(timezone.utc)
            case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().one()
            await record_candidates(db, case, cands, channel=channel, source_ts=ts, permalink=permalink,
                                    author={k: v for k, v in (("slack_user", user), ("author", prof.get("name"))) if v})
            await db.commit()  # seen + suggestions together: a crash before here redoes all, after here only the reply
    await _post_reply(tenant_id, token, case_id, channel, ts, event.get("thread_ts") or ts)


async def _post_reply(tenant_id: int, token: str, case_id: int, channel: str, ts: str, thread_ts: str) -> None:
    """Post the thread reply for a message's stored suggestions, unless one was already posted."""
    by_message = (WarRoomSuggestion.tenant_id == tenant_id, WarRoomSuggestion.slack_channel == channel,
                  WarRoomSuggestion.source_ts == ts)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(WarRoomSuggestion).where(*by_message).order_by(WarRoomSuggestion.id))).scalars().all()
        shown, more = split_for_reply(rows)
        if any(r.reply_ts for r in rows) or not any(r.status == "pending" for r in shown):
            return
        case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().one()
        blocks = suggestions_reply(shown, tenant_id, current=declared_milestones(case), more=more)
        ids = [r.id for r in shown]
    try:
        reply_ts = (await slack_call(token, "chat.postMessage", channel=channel, thread_ts=thread_ts,
                                     blocks=blocks, text="SOC-bot suggestions")).get("ts")
    except SlackError as e:
        # the suggestions are stored and stay answerable in the web UI either way
        if e.code in _TRANSIENT:
            raise  # Celery retries; the retry finds the message seen and only posts
        logger.exception("war room reply failed (tenant %s, channel %s)", tenant_id, channel)
        return
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(WarRoomSuggestion).where(
            WarRoomSuggestion.id.in_(ids), WarRoomSuggestion.tenant_id == tenant_id))).scalars().all()
        for s in rows:
            s.reply_ts = reply_ts
        if any(s.status != "pending" for s in rows):
            # answered in the web UI while the reply was being posted: redraw it so its buttons are not live
            queue_card(db, tenant_id, "war_room.rerender", case_id, {"reply_ts": reply_ts, "channel": channel})
        await db.commit()


async def prune() -> None:
    """Drop redelivery markers past Slack's retry window and retire suggestions nobody answered on a case that
    has been closed for a week."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=KEEP_DAYS)
    async with AsyncSessionLocal() as db:
        await db.execute(delete(WarRoomMessageSeen).where(WarRoomMessageSeen.created_at < cutoff))
        # ponytail: the case has no closed-at stamp; "closed and untouched for a week" stands in for it
        closed = select(Case.id).where(Case.status == CaseStatus.CLOSED,
                                       func.coalesce(Case.updated_at, Case.created_at) < cutoff)
        await db.execute(update(WarRoomSuggestion).where(
            WarRoomSuggestion.status == "pending", WarRoomSuggestion.case_id.in_(closed))
            .values(status="dismissed", resolved_at=now).execution_options(synchronize_session=False))
        await db.commit()


# acks_late + retries: a worker crash or a Slack blip re-runs the message, which process_message tolerates
@celery_app.task(bind=True, acks_late=True, autoretry_for=(Exception,), dont_autoretry_for=(SoftTimeLimitExceeded,),
                 retry_backoff=True, max_retries=3)
def war_room_message_task(self, tenant_id, event):
    _run_async(process_message(tenant_id, event))


@celery_app.task(acks_late=True)
def prune_war_room_task():
    _run_async(prune())
