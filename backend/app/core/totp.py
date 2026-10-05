"""RFC 6238 TOTP (SHA-1, 30 s, ±1 step) with replay protection. Stdlib only."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP, DIGITS, WINDOW = 30, 6, 1


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


async def consume_step(redis, user_id: int, step: int) -> bool:
    return bool(await redis.set(f"mfa:used:{user_id}:{step}", "1", nx=True, ex=90))
