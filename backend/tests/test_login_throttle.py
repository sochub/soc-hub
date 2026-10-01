"""Login hardening: Redis rate limiting, constant-time miss, case-insensitive email.

In-process ASGI against app.main.app with a throwaway tenant/user (deleted by id)
and an in-memory Redis fake injected via dependency_overrides.
"""
import asyncio
import logging
import secrets

import httpx
from sqlalchemy import delete

from app.api.v1 import auth as auth_api
from app.core import login_throttle, security
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User

URL = "/api/v1/auth/login/access-token"
PASSWORD = "Corr3ct-Horse-Battery"


class FakeRedis:
    """Tiny in-memory stand-in for the redis.asyncio commands the throttle uses (get/incr/expire/ttl/delete)."""

    def __init__(self, clock):
        self.clock, self.data = clock, {}  # key -> [value, expires_at|None]

    def _live(self, key):
        item = self.data.get(key)
        if item and item[1] is not None and item[1] <= self.clock():
            del self.data[key]
            return None
        return item

    async def get(self, key):
        item = self._live(key)
        return str(item[0]).encode() if item else None

    async def incr(self, key):
        item = self._live(key) or self.data.setdefault(key, [0, None])
        item[0] += 1
        return item[0]

    async def expire(self, key, seconds):
        if self._live(key):
            self.data[key][1] = self.clock() + seconds

    async def ttl(self, key):
        item = self._live(key)
        if not item:
            return -2
        return -1 if item[1] is None else int(item[1] - self.clock())

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


class DownRedis:
    async def _boom(self, *a, **kw):
        raise ConnectionError("redis is down")

    get = incr = expire = ttl = delete = _boom


async def _with_user(fn, redis, email=None):
    h = secrets.token_hex(3)
    email = email or f"throttle-{h}@example.test"
    ids = {}
    async with AsyncSessionLocal() as db:
        t = Tenant(name="throttle-test", slug=f"throttle-test-{h}")
        u = User(email=email, hashed_password=security.get_password_hash(PASSWORD), is_active=True)
        db.add_all([t, u])
        await db.flush()
        db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
        await db.commit()
        ids = {"tenant": t.id, "user": u.id}
    throttle = login_throttle.LoginThrottle(redis=redis)
    app.dependency_overrides[login_throttle.get_login_throttle] = lambda: throttle
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await fn(c, email)
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(TenantMembership).where(TenantMembership.user_id == ids["user"]))
            await db.execute(delete(User).where(User.id == ids["user"]))
            await db.execute(delete(Tenant).where(Tenant.id == ids["tenant"]))
            await db.commit()
        await engine.dispose()


def _login(c, email, password, ip="203.0.113.7"):
    return c.post(URL, data={"username": email, "password": password}, headers={"X-Forwarded-For": f"{ip}, 10.0.0.1"})


def test_sixth_failure_for_email_is_429_with_retry_after():
    now = [1000.0]

    async def scenario(c, email):
        codes = [(await _login(c, email, "wrong-pass")).status_code for _ in range(5)]
        assert codes == [401] * 5
        r = await _login(c, email, "wrong-pass")
        assert r.status_code == 429
        assert 0 < int(r.headers["Retry-After"]) <= 900
        # even the right password is refused while locked
        assert (await _login(c, email, PASSWORD)).status_code == 429
        now[0] += 901  # window expires
        assert (await _login(c, email, PASSWORD)).status_code == 200

    asyncio.run(_with_user(scenario, FakeRedis(lambda: now[0])))


def test_success_resets_email_counter():
    async def scenario(c, email):
        for _ in range(4):
            assert (await _login(c, email, "wrong-pass")).status_code == 401
        assert (await _login(c, email, PASSWORD)).status_code == 200
        codes = [(await _login(c, email, "wrong-pass")).status_code for _ in range(5)]
        assert codes == [401] * 5

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_ip_limit_enforced_across_emails():
    async def scenario(c, email):
        for i in range(20):
            r = await _login(c, f"nobody-{i}-{secrets.token_hex(2)}@example.test", "x", ip="198.51.100.9")
            assert r.status_code == 401, i
        r = await _login(c, email, PASSWORD, ip="198.51.100.9")
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
        # a different client IP is unaffected
        assert (await _login(c, email, PASSWORD, ip="198.51.100.10")).status_code == 200

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_redis_down_fails_open():
    async def scenario(c, email):
        assert (await _login(c, email, "wrong-pass")).status_code == 401
        r = await _login(c, email, PASSWORD)
        assert r.status_code == 200 and r.json()["access_token"]

    asyncio.run(_with_user(scenario, DownRedis()))


def test_unknown_email_still_verifies_password(monkeypatch):
    calls = []
    real = security.verify_password

    def spy(plain, hashed):
        calls.append(hashed)
        return real(plain, hashed)

    monkeypatch.setattr(security, "verify_password", spy)

    async def scenario(c, email):
        r = await _login(c, f"ghost-{secrets.token_hex(3)}@example.test", "whatever")
        assert r.status_code == 401
        assert calls == [auth_api._DUMMY_HASH]

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_mixed_case_email_logs_in(caplog):
    async def scenario(c, email):
        assert (await _login(c, "  " + email.upper() + " ", PASSWORD)).status_code == 200
        with caplog.at_level(logging.WARNING, logger="app.api.v1.auth"):
            assert (await _login(c, email.upper(), "S3cret-in-logs?")).status_code == 401
        text = caplog.text
        assert "203.0.113.7" in text and email in text and "S3cret-in-logs?" not in text

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))
