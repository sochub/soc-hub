"""RFC 6238 TOTP (SHA-1, 30 s, ±1 step) with replay protection. Stdlib only."""
import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP, DIGITS, WINDOW = 30, 6, 1

logger = logging.getLogger(__name__)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    s = secret_b32.upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def _hotp(key: bytes, counter: int, digits: int) -> str:
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_at(secret_b32: str, for_time: int, digits: int = DIGITS) -> str:
    return _hotp(_key(secret_b32), int(for_time) // STEP, digits)


def matching_step(secret_b32: str, code: str, now: Optional[float] = None) -> Optional[int]:
    code = (code or "").strip()
    if len(code) != DIGITS or not code.isdigit():
        return None
    key, step = _key(secret_b32), int((now if now is not None else time.time()) // STEP)
    hit = None
    for s in range(step - WINDOW, step + WINDOW + 1):
        if hmac.compare_digest(_hotp(key, s, DIGITS), code):
            hit = s  # no early return: same work for every code
    return hit


def provisioning_uri(secret_b32: str, email: str) -> str:
    return f"otpauth://totp/SOC%20Hub:{quote(email)}?secret={secret_b32}&issuer=SOC%20Hub"


# Single-use markers kept in this process while Redis is unreachable: key -> expires_at (monotonic).
# Consulted on every call (not only during an outage), so a marker set while Redis was down still
# blocks a replay after it comes back. Entries only exist for codes/challenges that already passed.
# ponytail: per-process; during an outage each worker remembers only its own markers.
_LOCAL_ONCE: dict[str, float] = {}
_LOCAL_ONCE_MAX = 10_000


def _local_set_once(key: str, ttl: int) -> bool:
    now = time.monotonic()
    exp = _LOCAL_ONCE.get(key)
    if exp is not None and exp > now:
        return False
    if len(_LOCAL_ONCE) >= _LOCAL_ONCE_MAX:
        for k in [k for k, e in _LOCAL_ONCE.items() if e <= now]:
            del _LOCAL_ONCE[k]
        while len(_LOCAL_ONCE) >= _LOCAL_ONCE_MAX:  # still full: drop the oldest insertion
            del _LOCAL_ONCE[next(iter(_LOCAL_ONCE))]
    _LOCAL_ONCE[key] = now + ttl
    return True


async def set_once(redis, key: str, ttl: int) -> bool:
    """Atomically mark `key` used for `ttl` seconds. False if it already was. Falls back to a bounded
    in-process set (with a warning) when Redis fails, so MFA sign-in survives a Redis outage."""
    exp = _LOCAL_ONCE.get(key)
    if exp is not None and exp > time.monotonic():
        return False
    try:
        return bool(await redis.set(key, "1", nx=True, ex=ttl))
    except Exception as exc:
        logger.warning("redis unavailable for single-use marker, using in-memory fallback: %s", type(exc).__name__)
        return _local_set_once(key, ttl)


async def consume_step(redis, subject: int | str, step: int) -> bool:
    return await set_once(redis, f"mfa:used:{subject}:{step}", 90)
