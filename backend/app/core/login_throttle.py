"""Redis-backed brute-force throttling for password login.

Every attempt makes a conditional, atomic reservation BEFORE the password is
checked: one Lua script (RESERVE_LUA) reads three fixed-window counters and,
only if all are below their limits, increments all three (EXPIRE NX sets the
window). If any counter is already at its limit, nothing is incremented and
the attempt is refused (HTTP 429, Retry-After = that key's remaining TTL) — so
rejected attempts consume no budget, and a concurrent burst can't race past
the limits. Counters (LOGIN_WINDOW_SECONDS window):

- (email, client IP)  LOGIN_MAX_PER_EMAIL_IP — the hard lock. Keyed on the IP
  too, so an attacker can't lock a victim out from a different address.
- email               LOGIN_MAX_PER_EMAIL — looser global cap, so guessing
  distributed across many IPs is still bounded. Only *allowed* attempts count,
  so one IP can push it up by at most LOGIN_MAX_PER_EMAIL_IP per window.
- client IP           LOGIN_MAX_PER_IP — across all emails (failures only:
  successful logins are refunded).

A successful login deletes the two email counters and refunds (DECRs, never
below 0) its IP slot.

If Redis is unreachable we log a warning and fall back to the same counters
kept in this process's memory (_LOCAL), so an outage weakens protection (each
worker counts on its own) rather than removing it or locking everyone out.
"""
import logging
import time
from typing import Optional

from fastapi import Request

from app.core.config import settings

logger = logging.getLogger(__name__)

# Requires Redis >= 7.0 (EXPIRE ... NX) and a single Redis node: the three keys
# are not hash-tagged, so the script is not Redis Cluster compatible.
#
# KEYS: (email,ip), email, ip counters. ARGV: window, then one limit per key.
# Returns {1, 0} when reserved, or {0, retry_after} when blocked — in which case
# nothing was incremented. retry_after is the largest TTL among the keys at
# their limit (or the window if none has a positive TTL). A blocking key that
# lost its TTL gets one (EXPIRE NX), so it can't stay locked forever.
RESERVE_LUA = """
local window = tonumber(ARGV[1])
local blocked = false
local blocked_ttl = -1
for i = 1, #KEYS do
  local count = tonumber(redis.call('GET', KEYS[i]) or '0')
  if count >= tonumber(ARGV[i + 1]) then
    blocked = true
    redis.call('EXPIRE', KEYS[i], window, 'NX')
    local ttl = redis.call('TTL', KEYS[i])
    if ttl > blocked_ttl then blocked_ttl = ttl end
  end
end
if blocked then
  if blocked_ttl <= 0 then blocked_ttl = window end
  return {0, blocked_ttl}
end
for i = 1, #KEYS do
  redis.call('INCR', KEYS[i])
  redis.call('EXPIRE', KEYS[i], window, 'NX')
end
return {1, 0}
"""

# KEYS[1]: per-IP counter. Refund one slot; never leave it at or below 0 (a key
# that expired since the reservation would otherwise be recreated at -1 with no TTL).
REFUND_LUA = """
local value = redis.call('DECR', KEYS[1])
if value <= 0 then redis.call('DEL', KEYS[1]) end
return value
"""


# Fallback counters used only while Redis is down: key -> (count, expires_at monotonic).
# ponytail: per-process dict; limits multiply by the worker count during an outage.
_LOCAL: dict[str, tuple[int, float]] = {}
_LOCAL_MAX_KEYS = 10_000


def _local_reserve(keys: list[str], limits: list[int], window: int) -> Optional[int]:
    """In-memory twin of RESERVE_LUA."""
    now = time.monotonic()
    if len(_LOCAL) > _LOCAL_MAX_KEYS:
        for k in [k for k, (_, exp) in _LOCAL.items() if exp <= now]:
            del _LOCAL[k]
    live = {k: v for k in keys if (v := _LOCAL.get(k)) and v[1] > now}
    blocked = [live[k][1] for k, lim in zip(keys, limits) if k in live and live[k][0] >= lim]
    if blocked:
        return max(1, int(max(blocked) - now))
    for k in keys:
        count, exp = live.get(k, (0, now + window))
        _LOCAL[k] = (count + 1, exp)
    return None


def _local_refund(ip_key: str, email_keys: tuple[str, str]) -> None:
    for k in email_keys:
        _LOCAL.pop(k, None)
    count, exp = _LOCAL.get(ip_key, (0, 0.0))
    if count <= 1:
        _LOCAL.pop(ip_key, None)
    else:
        _LOCAL[ip_key] = (count - 1, exp)


def client_ip(request: Request) -> str:
    """Client IP for throttling: nginx's X-Real-IP, else the socket peer.

    X-Forwarded-For is deliberately ignored: its first hop is client-supplied
    and would let an attacker rotate IP buckets. Trusting X-Real-IP is safe
    because the backend port is published on 127.0.0.1 only, so only nginx
    (which sets it from $remote_addr) or a local process can reach us.
    """
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


class LoginThrottle:
    _parent: Optional["LoginThrottle"] = None  # set by namespaced(): borrow its lazy Redis client

    def __init__(self, redis=None, *, window: Optional[int] = None, max_email_ip: Optional[int] = None,
                 max_email: Optional[int] = None, max_ip: Optional[int] = None, prefix: str = "login"):
        if not prefix or ":" in prefix:
            raise ValueError("LoginThrottle prefix must be a non-empty string without ':'")
        # Namespace for every Redis key ("<prefix>:attempts:..."), so counters
        # for different purposes (login, MFA codes, password checks) can never
        # collide even if a key value looks like another purpose's key.
        self.prefix = prefix
        self._redis = redis
        self._reserve_script = self._refund_script = None
        self.window = window if window is not None else settings.LOGIN_WINDOW_SECONDS
        self.max_email_ip = max_email_ip if max_email_ip is not None else settings.LOGIN_MAX_PER_EMAIL_IP
        self.max_email = max_email if max_email is not None else settings.LOGIN_MAX_PER_EMAIL
        self.max_ip = max_ip if max_ip is not None else settings.LOGIN_MAX_PER_IP
        for name in ("window", "max_email_ip", "max_email", "max_ip"):
            if getattr(self, name) < 1:
                raise ValueError(f"LoginThrottle {name} must be >= 1, got {getattr(self, name)}")

    @property
    def redis(self):
        if self._redis is None and self._parent is not None:
            self._redis = self._parent.redis
        if self._redis is None:
            import redis.asyncio as aioredis

            # A *blocking* pool: a burst larger than the pool waits briefly for
            # a connection instead of raising "Too many connections" — which
            # the fail-open path would otherwise turn into a limit bypass.
            pool = aioredis.BlockingConnectionPool.from_url(
                settings.REDIS_URL, max_connections=50, timeout=5,
                socket_connect_timeout=1, socket_timeout=1,
            )
            self._redis = aioredis.Redis(connection_pool=pool)
        return self._redis

    def namespaced(self, prefix: str, **limits) -> "LoginThrottle":
        """A throttle sharing this one's Redis client, with its own key namespace
        (and optionally its own limits: window/max_email_ip/max_email/max_ip)."""
        opts = {"window": self.window, "max_email_ip": self.max_email_ip,
                "max_email": self.max_email, "max_ip": self.max_ip, **limits}
        clone = LoginThrottle(self._redis, prefix=prefix, **opts)
        if self._redis is None:  # share the lazily-created default client
            clone._parent = self
        return clone

    def _ip_key(self, ip: str) -> str:
        return f"{self.prefix}:attempts:ip:{ip}"

    def _email_keys(self, email: str, ip: str) -> tuple[str, str]:
        p = self.prefix
        return f"{p}:attempts:email_ip:{email}|{ip}", f"{p}:attempts:email:{email}"

    async def reserve(self, email: str, ip: str) -> Optional[int]:
        """Reserve one attempt. Returns Retry-After seconds if blocked (nothing counted), else None."""
        keys = [*self._email_keys(email, ip), self._ip_key(ip)]
        args = [self.window, self.max_email_ip, self.max_email, self.max_ip]
        try:
            if self._reserve_script is None:
                self._reserve_script = self.redis.register_script(RESERVE_LUA)
            allowed, ttl = await self._reserve_script(keys=keys, args=args)
        except Exception as exc:
            logger.warning("login throttle unavailable (reserve), using in-memory fallback: %s", exc)
            return _local_reserve(keys, args[1:], self.window)
        return None if int(allowed) == 1 else int(ttl)

    async def reset(self, email: str, ip: str) -> None:
        """Successful login: clear the email counters and refund the IP slot.

        The per-IP limit therefore counts failed attempts only (a NATed office
        or Docker Desktop, where every client shares one IP, isn't throttled by
        its own successful logins). The reservation itself stays atomic.
        """
        ip_key = self._ip_key(ip)
        try:
            await self.redis.delete(*self._email_keys(email, ip))
            if self._refund_script is None:
                self._refund_script = self.redis.register_script(REFUND_LUA)
            await self._refund_script(keys=[ip_key])  # atomic DECR, DEL if <= 0
        except Exception as exc:
            logger.warning("login throttle unavailable (reset): %s", exc)
            _local_refund(ip_key, self._email_keys(email, ip))


_throttle = LoginThrottle()


# Current-password checks (change password, turn MFA on/off): same limits as login,
# separate namespace so a login "username" can never touch these counters.
_pwd_throttle = _throttle.namespaced("pwd")

# Audit-log exports: a quota per user (each export scans up to 50k rows), not a lock.
_export_throttle = _throttle.namespaced("audit_export", window=600, max_email_ip=5, max_email=5, max_ip=20)


def get_login_throttle() -> LoginThrottle:
    """FastAPI dependency; tests override it with a LoginThrottle on a fake Redis."""
    return _throttle


def get_pwd_throttle() -> LoginThrottle:
    """Throttle for current-password checks (keys pwd:attempts:...); tests override it."""
    return _pwd_throttle


def get_export_throttle() -> LoginThrottle:
    """Quota for audit-log exports (keyed on the user id); tests override it."""
    return _export_throttle
