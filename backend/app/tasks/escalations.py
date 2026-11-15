import logging
from typing import Optional

from app.tasks.runner import _run_async
from app.worker import celery_app


@celery_app.task(acks_late=True)
def escalation_sweep_task():
    from app.escalations.service import sweep
    return _run_async(sweep())


async def _post_channel(row_id: int):
    from app.db.session import AsyncSessionLocal
    from app.notifications.delivery import post_escalation_channel
    async with AsyncSessionLocal() as db:
        return await post_escalation_channel(db, row_id)


@celery_app.task(acks_late=True)
def post_escalation_channel_task(row_id: int):
    return _run_async(_post_channel(row_id))


PD_API = "https://api.pagerduty.com"
PD_TIMEOUT = 5
PD_USER_TTL = 3600
_logger = logging.getLogger(__name__)


def _redis():
    import redis.asyncio as aioredis
    from app.core.config import settings
    return aioredis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)


async def _pd_get(api_key: str, path: str) -> dict:
    """GET a PagerDuty REST resource (read-only key). `path` segments are validated by the caller."""
    import httpx
    async with httpx.AsyncClient(timeout=PD_TIMEOUT, follow_redirects=False) as client:
        r = await client.get(f"{PD_API}/{path}", headers={
            "Authorization": f"Token token={api_key}", "Accept": "application/vnd.pagerduty+json;version=2"})
        r.raise_for_status()
        return r.json()


async def _pd_email(tenant_id: int, api_key: Optional[str], user_id: Optional[str]) -> Optional[str]:
    """The PagerDuty user's email, cached 1 h in Redis. Any failure -> None (the ack is then logged unmatched)."""
    if not api_key or not user_id:
        return None
    key = f"pd:user:{tenant_id}:{user_id}"
    r = None
    try:
        r = _redis()
        cached = await r.get(key)
        if cached:
            return cached.decode() if isinstance(cached, bytes) else cached
    except Exception as e:
        _logger.debug("pd user cache read failed: %s", type(e).__name__)
    email = None
    try:
        email = ((await _pd_get(api_key, f"users/{user_id}")).get("user") or {}).get("email")
        email = email if isinstance(email, str) and email.strip() else None
    except Exception as e:
        _logger.warning("PagerDuty user lookup failed tenant=%s: %s", tenant_id, type(e).__name__)
    if r is not None:
        try:
            if email:
                await r.set(key, email, ex=PD_USER_TTL)
            await r.aclose()
        except Exception:
            _logger.debug("pd user cache write failed", exc_info=True)
    return email


async def _process_ack(tenant_id: int, vendor: str, dedup_key: Optional[str], agent_ref: Optional[str],
                       incident_id: Optional[str] = None) -> str:
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.escalations.service import accept_external
    from app.models.escalation import EscalationIntegration
    from app.utils.crypto import decrypt

    if vendor not in ("pagerduty", "opsgenie"):
        return "ignored"
    async with AsyncSessionLocal() as db:
        integ = (await db.execute(select(EscalationIntegration).where(
            EscalationIntegration.tenant_id == tenant_id))).scalars().first()
    email, actor_ref = (agent_ref, None) if vendor == "opsgenie" else (None, agent_ref)
    if vendor == "pagerduty":
        api_key = None
        if integ is not None and integ.pagerduty_api_key_enc:
            try:
                api_key = decrypt(integ.pagerduty_api_key_enc)
            except Exception:
                _logger.warning("PagerDuty API key undecryptable tenant=%s", tenant_id)
        if dedup_key is None and incident_id and api_key:
            try:
                dedup_key = ((await _pd_get(api_key, f"incidents/{incident_id}")).get("incident") or {}).get(
                    "incident_key")
            except Exception as e:
                _logger.warning("PagerDuty incident lookup failed tenant=%s: %s", tenant_id, type(e).__name__)
        if not isinstance(dedup_key, str):
            return "ignored"
        email = await _pd_email(tenant_id, api_key, agent_ref)
    async with AsyncSessionLocal() as db:
        result = await accept_external(db, tenant_id=tenant_id, dedup_key=dedup_key, email=email, via=vendor,
                                       actor_ref=actor_ref)
        await db.commit()
    return result


@celery_app.task(acks_late=True)
def process_ack_task(tenant_id: int, vendor: str, dedup_key: Optional[str], agent_ref: Optional[str],
                     incident_id: Optional[str] = None):
    return _run_async(_process_ack(tenant_id, vendor, dedup_key, agent_ref, incident_id=incident_id))
