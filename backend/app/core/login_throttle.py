"""Redis-backed brute-force throttling for password login.

Fixed-window counters of FAILED attempts, keyed per normalized email and per
client IP. Once a key reaches its limit, further attempts are refused (HTTP 429)
until the key's TTL — the remainder of the window — runs out. A successful
login clears the email counter.

Fails OPEN: if Redis is unreachable we log a warning and allow the attempt, so
a Redis outage degrades protection rather than locking everyone out.
"""
import logging
from typing import Optional

from fastapi import Request

from app.core.config import settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_EMAIL = 5
MAX_FAILURES_PER_IP = 20


def client_ip(request: Request) -> str:
    """First X-Forwarded-For hop (set by nginx), else the socket peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


class LoginThrottle:
    def __init__(self, redis=None, *, window: int = WINDOW_SECONDS,
                 max_email: int = MAX_FAILURES_PER_EMAIL, max_ip: int = MAX_FAILURES_PER_IP):
        self._redis = redis
        self.window, self.max_email, self.max_ip = window, max_email, max_ip

    @property
    def redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1
            )
        return self._redis

    @staticmethod
    def _keys(email: str, ip: str) -> tuple[str, str]:
        return f"login:fail:email:{email}", f"login:fail:ip:{ip}"

    async def retry_after(self, email: str, ip: str) -> Optional[int]:
        """Seconds until the caller may retry, or None if not throttled."""
        try:
            wait = 0
            for key, limit in zip(self._keys(email, ip), (self.max_email, self.max_ip)):
                count = await self._count(key)
                if count >= limit:
                    ttl = await self.redis.ttl(key)
                    wait = max(wait, ttl if ttl > 0 else self.window)
            return wait or None
        except Exception as exc:  # fail open
            logger.warning("login throttle unavailable (check): %s", exc)
            return None

    async def _count(self, key: str) -> int:
        return int(await self.redis.get(key) or 0)

    async def record_failure(self, email: str, ip: str) -> None:
        try:
            for key in self._keys(email, ip):
                # ttl < 0 also repairs a key whose EXPIRE was lost (crash between
                # the two calls), which would otherwise lock the key forever.
                if await self.redis.incr(key) == 1 or await self.redis.ttl(key) < 0:
                    await self.redis.expire(key, self.window)
        except Exception as exc:
            logger.warning("login throttle unavailable (record): %s", exc)

    async def reset(self, email: str) -> None:
        try:
            await self.redis.delete(self._keys(email, "")[0])
        except Exception as exc:
            logger.warning("login throttle unavailable (reset): %s", exc)


_throttle = LoginThrottle()


def get_login_throttle() -> LoginThrottle:
    """FastAPI dependency; tests override it with a LoginThrottle on a fake Redis."""
    return _throttle
