import secrets

import pytest
import redis.asyncio as aioredis

from app.core.config import settings
from app.enrichment.config import EnrichmentSettings
from app.enrichment.ratelimit import RateLimiter, limits_for


def test_limits_for():
    s = EnrichmentSettings(vt_per_minute=4, vt_per_day=500)
    assert limits_for("virustotal", s) == [(4, 60), (500, 86400)]
    assert limits_for("urlhaus", s) == [(60, 60)] and limits_for("threatfox", s) == [(60, 60)]
    assert limits_for("rdap", s) == [(30, 60)] and limits_for("crtsh", s) == [(1, 5)]


@pytest.mark.asyncio
async def test_acquire_blocks_and_is_atomic():
    r = aioredis.from_url(settings.REDIS_URL)
    prefix = f"ti:test:{secrets.token_hex(4)}"
    rl = RateLimiter(r, prefix=prefix)
    try:
        assert await rl.acquire(1, "virustotal", [(2, 60), (3, 86400)]) is None
        assert await rl.acquire(1, "virustotal", [(2, 60), (3, 86400)]) is None
        wait = await rl.acquire(1, "virustotal", [(2, 60), (3, 86400)])
        assert wait is not None and 0 < wait <= 60
        # the refused call must not have consumed the daily budget
        day = await r.get(f"{prefix}:1:virustotal:86400")
        assert int(day) == 2
        # other tenant unaffected
        assert await rl.acquire(2, "virustotal", [(2, 60)]) is None
        # crtsh is global across tenants
        assert await rl.acquire(1, "crtsh", [(1, 5)]) is None
        assert await rl.acquire(2, "crtsh", [(1, 5)]) is not None
    finally:
        keys = [k async for k in r.scan_iter(f"{prefix}:*")]
        if keys:
            await r.delete(*keys)
        await r.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_only_self_created_client():
    class FakeRedis:
        closed = False

        async def aclose(self):
            self.closed = True

    injected = FakeRedis()
    rl = RateLimiter(injected)
    await rl.aclose()
    assert injected.closed is False

    own = RateLimiter()
    client = own.redis  # lazily created by the limiter itself
    closed = []

    async def fake_aclose():
        closed.append(True)
    client.aclose = fake_aclose
    await own.aclose()
    assert closed == [True]
    await RateLimiter().aclose()  # never-used limiter: no client, no error
