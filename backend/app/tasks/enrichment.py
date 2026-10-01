"""Celery task: run enrichment sources for one indicator, cache results, respect limits."""
import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.ai.errors import scrub
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.enrichment.config import SOURCE_KEY, load_settings, runnable_sources
from app.enrichment.indicators import normalise, should_skip, tlp_allows
from app.enrichment.ratelimit import RateLimiter, limits_for
from app.enrichment.sources import REGISTRY
from app.models.enrichment_result import EnrichmentResult
from app.worker import celery_app

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 10
_LOG = "ti_lookup tenant=%s source=%s type=%s outcome=%s duration_ms=%d"
_DECRYPT_MSG = "credentials could not be decrypted — re-enter in Integrations"
_CLEARS = ("skipped", "error", "rate_limited")
_RESULT_FIELDS = ("verdict", "score", "summary", "link")


async def upsert_result(db, tenant_id, itype, value, source, **fields):
    """Race-safe insert-or-update of one (tenant, indicator, source) row; commits."""
    if fields.get("status") in _CLEARS:
        # A non-result must not keep showing an older verdict. "pending" keeps it on purpose:
        # the panel shows the previous result while re-checking.
        fields.update(dict.fromkeys(_RESULT_FIELDS))
    fields["updated_at"] = datetime.now(timezone.utc)
    stmt = pg_insert(EnrichmentResult).values(tenant_id=tenant_id, indicator_type=itype, indicator_value=value,
                                               source=source, **fields)
    stmt = stmt.on_conflict_do_update(constraint="uq_enrichment_result",
                                      set_={k: stmt.excluded[k] for k in fields}).returning(EnrichmentResult)
    row = (await db.execute(stmt, execution_options={"populate_existing": True})).scalars().one()
    await db.commit()
    return row


def _retry(tenant_id, itype, value, source, attempt, countdown):
    enrich_indicator.apply_async((tenant_id, itype, value, "white", True),
                                 {"only": [source], "attempt": attempt}, countdown=countdown)


async def run_enrichment(db, tenant_id, itype, value, *, sources: Optional[List[str]] = None, force=False,
                         attempt=0, limiter=None, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = await load_settings(db, tenant_id)
    own_limiter = limiter is None
    limiter = limiter or RateLimiter()
    try:
        return await _run(db, tenant_id, itype, value, s, sources, force, attempt, limiter, now)
    finally:
        if own_limiter:
            await limiter.aclose()


async def _run(db, tenant_id, itype, value, s, sources, force, attempt, limiter, now) -> dict:
    secrets_ = list(s.keys.values())
    runnable = [n for n in runnable_sources(s) if n in REGISTRY and itype in REGISTRY[n].TYPES]
    if sources is not None:
        runnable = [n for n in runnable if n in sources]
    out = {}
    if s.key_error:
        for name in SOURCE_KEY:
            if (s.sources.get(name, True) and name in REGISTRY and itype in REGISTRY[name].TYPES
                    and (sources is None or name in sources)):
                await _guarded(db, tenant_id, itype, value, name, out, secrets_,
                               _decrypt_error(db, tenant_id, itype, value, name, out))
    skip = should_skip(itype, value, s.internal_domains)
    fresh_after = now - timedelta(hours=s.cache_ttl_hours)
    for name in runnable:
        await _guarded(db, tenant_id, itype, value, name, out, secrets_, _one_source(
            db, tenant_id, itype, value, name, s, out, secrets_, skip, force, fresh_after, attempt, limiter, now))
    return out


async def _guarded(db, tenant_id, itype, value, name, out, secrets_, coro):
    """Per-source isolation: a failure outside lookup() (Redis, DB, broker) only fails this source.

    The step coroutine logs its own ti_lookup line on success; on failure the line is logged here.
    """
    start = time.monotonic()
    try:
        await coro
    except Exception as e:
        out[name] = "error"
        try:
            await db.rollback()
            await upsert_result(db, tenant_id, itype, value, name, status="error",
                                error=scrub(f"{name} failed ({type(e).__name__})", secrets_))
        except Exception:
            pass  # best effort: the row may stay as it was
        # Still the single ti_lookup line for this source, at WARNING so infrastructure faults stand out.
        logger.warning(_LOG, tenant_id, name, itype, "error", int((time.monotonic() - start) * 1000))


async def _decrypt_error(db, tenant_id, itype, value, name, out):
    await upsert_result(db, tenant_id, itype, value, name, status="error", error=_DECRYPT_MSG)
    out[name] = "error"
    logger.info(_LOG, tenant_id, name, itype, "error", 0)


async def _one_source(db, tenant_id, itype, value, name, s, out, secrets_, skip, force, fresh_after,
                      attempt, limiter, now):
    if skip:
        await upsert_result(db, tenant_id, itype, value, name, status="skipped", verdict=None, error=None)
        out[name] = "skipped"
        logger.info(_LOG, tenant_id, name, itype, "skipped", 0)
        return
    if not force:
        row = (await db.execute(select(EnrichmentResult).where(
            EnrichmentResult.tenant_id == tenant_id, EnrichmentResult.indicator_type == itype,
            EnrichmentResult.indicator_value == value, EnrichmentResult.source == name))).scalars().first()
        if row and row.status in ("ok", "not_found") and row.fetched_at and row.fetched_at > fresh_after:
            out[name] = row.status
            logger.info(_LOG, tenant_id, name, itype, row.status, 0)
            return
    wait = await limiter.acquire(tenant_id, name, limits_for(name, s))
    if wait is not None:
        if wait >= 3600:
            err = "VirusTotal daily quota reached" if name == "virustotal" else f"{name} quota reached"
            await upsert_result(db, tenant_id, itype, value, name, status="rate_limited", error=err)
            status = "rate_limited"
        elif attempt >= MAX_ATTEMPTS:
            await upsert_result(db, tenant_id, itype, value, name, status="rate_limited", error=f"{name} rate limit")
            status = "rate_limited"
        else:
            await upsert_result(db, tenant_id, itype, value, name, status="pending", error=None)
            _retry(tenant_id, itype, value, name, attempt=attempt + 1, countdown=wait)
            status = "pending"
        out[name] = status
        logger.info(_LOG, tenant_id, name, itype, status, 0)
        return
    start = time.monotonic()
    key = s.keys.get(SOURCE_KEY.get(name, ""), None)
    try:
        res = await REGISTRY[name].lookup(itype, value, key)
    except Exception as e:  # a source bug must never block the others
        res = None
        err = f"{name} failed ({type(e).__name__})"
    if res is None:
        await upsert_result(db, tenant_id, itype, value, name, status="error", error=scrub(err, secrets_))
        status = "error"
    else:
        await upsert_result(db, tenant_id, itype, value, name, status=res.status, verdict=res.verdict,
                            score=res.score, summary=res.summary, link=res.link,
                            error=scrub(res.error, secrets_) if res.error else None,
                            fetched_at=now if res.status in ("ok", "not_found") else None)
        status = res.status
    out[name] = status
    logger.info(_LOG, tenant_id, name, itype, status, int((time.monotonic() - start) * 1000))


class _RedisLock:
    def __init__(self):
        self._r = None

    async def acquire(self, key: str) -> bool:
        if self._r is None:
            import redis.asyncio as aioredis
            self._r = aioredis.from_url(settings.REDIS_URL)
        return bool(await self._r.set(key, "1", nx=True, ex=60))

    async def aclose(self) -> None:
        if self._r is not None:
            await self._r.aclose()
            self._r = None


def _lock():
    return _RedisLock()


async def _entry(tenant_id, raw_type, value, tlp, force, only=None, attempt=0, *, lock=None, limiter=None):
    if not settings.ENRICHMENT_ENABLED:  # kill switch also stops already-queued tasks and pending retries
        return
    norm = normalise(raw_type, value)
    if norm is None:
        return
    itype, v = norm
    async with AsyncSessionLocal() as db:
        if not force:
            s = await load_settings(db, tenant_id)
            if not tlp_allows(tlp or s.artifact_tlp, s.auto_max_tlp):
                return
            lock = lock or _lock()
            if not await lock.acquire(f"ti:lock:{tenant_id}:{itype}:{hashlib.sha256(v.encode()).hexdigest()}"):
                return
        await run_enrichment(db, tenant_id, itype, v, sources=only, force=force, attempt=attempt, limiter=limiter)


@celery_app.task(acks_late=True)
def enrich_indicator(tenant_id, raw_type, value, tlp, force=False, only=None, attempt=0):
    async def main():
        # Redis clients are bound to this asyncio.run() loop, so they are created here and closed here.
        lock, limiter = _lock(), RateLimiter()
        try:
            await _entry(tenant_id, raw_type, value, tlp, force, only, attempt, lock=lock, limiter=limiter)
        finally:
            for closer in (lock.aclose, limiter.aclose):
                try:
                    await closer()
                except Exception:
                    logger.warning("ti_task redis close failed tenant=%s", tenant_id)
            # asyncio.run() makes a new loop each call; pooled connections from the old loop must go.
            await engine.dispose()
    asyncio.run(main())
