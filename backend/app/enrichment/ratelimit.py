"""Per-tenant (crt.sh: global) fixed-window rate limits in Redis; all-or-nothing across windows."""
from typing import List, Optional, Tuple

ACQUIRE_LUA = """
for i = 1, #KEYS do
  local count = tonumber(redis.call('GET', KEYS[i]) or '0')
  if count >= tonumber(ARGV[2*i-1]) then
    local ttl = redis.call('TTL', KEYS[i])
    if ttl < 1 then ttl = tonumber(ARGV[2*i]) end
    return ttl
  end
end
for i = 1, #KEYS do
  redis.call('INCR', KEYS[i])
  redis.call('EXPIRE', KEYS[i], ARGV[2*i], 'NX')
end
return -1
"""

_FIXED = {"urlhaus": [(60, 60)], "threatfox": [(60, 60)], "rdap": [(30, 60)], "crtsh": [(1, 5)]}


def limits_for(source: str, s) -> List[Tuple[int, int]]:
    if source == "virustotal":
        return [(s.vt_per_minute, 60), (s.vt_per_day, 86400)]
    return list(_FIXED[source])


class RateLimiter:
    def __init__(self, redis=None, prefix: str = "ti:rl"):
        self._redis = redis
        self.prefix = prefix

    @property
    def redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            from app.core.config import settings
            self._redis = aioredis.from_url(settings.REDIS_URL)
        return self._redis

    async def acquire(self, tenant_id: int, source: str, limits) -> Optional[int]:
        scope = "global" if source == "crtsh" else str(tenant_id)
        keys = [f"{self.prefix}:{scope}:{source}:{w}" for _, w in limits]
        args = [x for m, w in limits for x in (m, w)]
        ttl = int(await self.redis.eval(ACQUIRE_LUA, len(keys), *keys, *args))
        return None if ttl < 0 else ttl
