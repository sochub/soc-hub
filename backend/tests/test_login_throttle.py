"""Login hardening: atomic Redis rate limiting, constant-time miss, case-insensitive email.

In-process ASGI against app.main.app with a throwaway tenant/user (deleted by id)
and an in-memory Redis fake injected via dependency_overrides.
"""
import asyncio
import logging
import secrets

import httpx
import pytest
from sqlalchemy import delete

from app.api.v1 import auth as auth_api
from app.core import login_throttle, security
from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User

URL = "/api/v1/auth/login/access-token"
PASSWORD = "Corr3ct-Horse-Battery"


class FakeRedis:
    """In-memory stand-in for the redis.asyncio commands the throttle uses.

    The reservation script is mirrored in Python (_reserve_script) and runs in
    one synchronous step, so — like a Redis Lua script — nothing interleaves.
    """

    def __init__(self, clock):
        self.clock, self.data = clock, {}  # key -> [value, expires_at|None]

    def _live(self, key):
        item = self.data.get(key)
        if item and item[1] is not None and item[1] <= self.clock():
            del self.data[key]
            return None
        return item

    def _incr(self, key):
        item = self._live(key) or self.data.setdefault(key, [0, None])
        item[0] += 1
        return item[0]

    def _expire(self, key, seconds, nx=False):
        item = self._live(key)
        if not item or (nx and item[1] is not None):
            return False
        item[1] = self.clock() + seconds
        return True

    def _ttl(self, key):
        item = self._live(key)
        if not item:
            return -2
        return -1 if item[1] is None else int(item[1] - self.clock())

    def _decr(self, key):
        item = self._live(key) or self.data.setdefault(key, [0, None])
        item[0] -= 1
        return item[0]

    def _delete(self, *keys):
        return sum(self.data.pop(k, None) is not None for k in keys)

    def register_script(self, lua):
        scripts = {login_throttle.RESERVE_LUA: self._reserve_script,
                   getattr(login_throttle, "REFUND_LUA", None): self._refund_script}
        assert lua is not None and lua in scripts
        return scripts[lua]

    async def _reserve_script(self, keys, args):
        """Python mirror of login_throttle.RESERVE_LUA. Runs without awaiting,
        so — like a Redis script — nothing can interleave with it."""
        window, limits = int(args[0]), [int(a) for a in args[1:]]
        blocked, blocked_ttl = False, -1
        for key, limit in zip(keys, limits):
            item = self._live(key)
            if (item[0] if item else 0) >= limit:
                blocked = True
                self._expire(key, window, nx=True)  # repair a TTL-less counter
                blocked_ttl = max(blocked_ttl, self._ttl(key))
        if blocked:
            return [0, blocked_ttl if blocked_ttl > 0 else window]
        for key in keys:
            self._incr(key)
            self._expire(key, window, nx=True)
        return [1, 0]

    async def _refund_script(self, keys, args=()):
        """Python mirror of login_throttle.REFUND_LUA (DECR; DEL if <= 0)."""
        value = self._decr(keys[0])
        if value <= 0:
            self._delete(keys[0])
        return value

    async def delete(self, *keys):
        return self._delete(*keys)


class DownRedis:
    def register_script(self, lua):
        async def run(keys, args):
            raise ConnectionError("redis is down")
        return run

    async def delete(self, *keys):
        raise ConnectionError("redis is down")


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


def _login(c, email, password, ip="203.0.113.7", xff="1.2.3.4"):
    # nginx sets X-Real-IP from $remote_addr; X-Forwarded-For is client-controllable noise here.
    return c.post(URL, data={"username": email, "password": password},
                  headers={"X-Real-IP": ip, "X-Forwarded-For": xff})


def test_sixth_attempt_for_email_and_ip_is_429_with_retry_after():
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


def test_success_resets_email_counters():
    async def scenario(c, email):
        for _ in range(4):
            assert (await _login(c, email, "wrong-pass")).status_code == 401
        assert (await _login(c, email, PASSWORD)).status_code == 200
        codes = [(await _login(c, email, "wrong-pass")).status_code for _ in range(5)]
        assert codes == [401] * 5

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_attacker_ip_cannot_lock_out_victim_on_other_ip():
    async def scenario(c, email):
        for _ in range(6):
            await _login(c, email, "wrong-pass", ip="198.51.100.66")
        assert (await _login(c, email, PASSWORD, ip="198.51.100.66")).status_code == 429
        assert (await _login(c, email, PASSWORD, ip="203.0.113.20")).status_code == 200

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_global_per_email_cap_limits_distributed_guessing():
    async def scenario(c, email):
        for n in range(10):  # 10 IPs x 3 = 30 attempts, each IP under its own limits
            for _ in range(3):
                assert (await _login(c, email, "wrong-pass", ip=f"192.0.2.{n}")).status_code == 401
        r = await _login(c, email, PASSWORD, ip="192.0.2.200")
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_rejected_attempts_consume_no_budget():
    r = FakeRedis(lambda: 1000.0)

    async def scenario(c, email):
        codes = [(await _login(c, email, "wrong-pass", ip="198.51.100.66")).status_code for _ in range(40)]
        allowed = codes.count(401)
        assert allowed == settings.LOGIN_MAX_PER_EMAIL_IP and codes.count(429) == 40 - allowed, codes
        global_count = r._live(f"login:attempts:email:{email}")[0]
        assert global_count <= allowed, global_count
        # the victim on another IP is not locked out by the attacker's rejected attempts
        assert (await _login(c, email, PASSWORD, ip="203.0.113.20")).status_code == 200

    asyncio.run(_with_user(scenario, r))


def test_31_distributed_attempts_lock_email_globally():
    async def scenario(c, email):
        codes = [(await _login(c, email, "wrong-pass", ip=f"192.0.2.{n}")).status_code for n in range(31)]
        assert codes[:settings.LOGIN_MAX_PER_EMAIL] == [401] * settings.LOGIN_MAX_PER_EMAIL
        assert codes[settings.LOGIN_MAX_PER_EMAIL:] == [429] * (31 - settings.LOGIN_MAX_PER_EMAIL)
        r = await _login(c, email, PASSWORD, ip="192.0.2.250")
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


@pytest.mark.parametrize("kw", [{"max_email_ip": 0}, {"max_email": 0}, {"max_ip": 0}, {"window": 0}, {"max_ip": -1}])
def test_limits_below_one_are_rejected(kw):
    with pytest.raises(ValueError):
        login_throttle.LoginThrottle(redis=FakeRedis(lambda: 1000.0), **kw)


def test_blocked_counter_without_ttl_is_repaired():
    r = FakeRedis(lambda: 1000.0)
    t = login_throttle.LoginThrottle(redis=r)
    key = t._email_keys("victim@example.test", "192.0.2.9")[0]
    r.data[key] = [settings.LOGIN_MAX_PER_EMAIL_IP, None]  # at limit, EXPIRE was lost
    assert asyncio.run(t.reserve("victim@example.test", "192.0.2.9")) == settings.LOGIN_WINDOW_SECONDS
    assert r._ttl(key) == settings.LOGIN_WINDOW_SECONDS


def test_ip_limit_enforced_across_emails():
    async def scenario(c, email):
        for i in range(20):
            # a different spoofed X-Forwarded-For each time must not spread the attempts across buckets
            r = await _login(c, f"nobody-{i}-{secrets.token_hex(2)}@example.test", "x",
                             ip="198.51.100.9", xff=f"10.9.{i}.1")
            assert r.status_code == 401, i
        r = await _login(c, email, PASSWORD, ip="198.51.100.9", xff="1.2.3.4")
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
        # X-Real-IP (set by nginx) selects the bucket: another client is unaffected
        assert (await _login(c, email, PASSWORD, ip="198.51.100.10", xff="1.2.3.4")).status_code == 200

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_ip_limit_counts_failures_only():
    async def scenario(c, email):
        ip = "198.51.100.77"
        for i in range(25):  # successes never consume the per-IP budget
            assert (await _login(c, email, PASSWORD, ip=ip)).status_code == 200, i
        for i in range(20):
            r = await _login(c, f"nobody-{i}-{secrets.token_hex(2)}@example.test", "x", ip=ip)
            assert r.status_code == 401, i
        r = await _login(c, email, PASSWORD, ip=ip)
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_ip_refund_never_goes_negative():
    r = FakeRedis(lambda: 1000.0)
    t = login_throttle.LoginThrottle(redis=r)
    # key already expired/absent when the success is refunded
    asyncio.run(t.reset("someone@example.test", "198.51.100.78"))
    assert r._ttl("login:attempts:ip:198.51.100.78") == -2


def test_concurrent_burst_reaches_password_check_at_most_limit_times(monkeypatch):
    calls = []
    real = security.verify_password

    def spy(plain, hashed):
        calls.append(1)
        return real(plain, hashed)

    monkeypatch.setattr(security, "verify_password", spy)

    async def scenario(c, email):
        rs = await asyncio.gather(*[_login(c, email, f"guess-{i}") for i in range(50)])
        codes = sorted(r.status_code for r in rs)
        assert len(calls) <= settings.LOGIN_MAX_PER_EMAIL_IP, len(calls)
        assert codes.count(429) >= 50 - settings.LOGIN_MAX_PER_EMAIL_IP, codes

    asyncio.run(_with_user(scenario, FakeRedis(lambda: 1000.0)))


def test_client_ip_prefers_x_real_ip_and_ignores_xff():
    from starlette.requests import Request

    def req(headers):
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                        "client": ("172.18.0.5", 5555)})

    assert login_throttle.client_ip(req({"X-Real-IP": "203.0.113.7", "X-Forwarded-For": "1.2.3.4"})) == "203.0.113.7"
    assert login_throttle.client_ip(req({"X-Forwarded-For": "1.2.3.4"})) == "172.18.0.5"
    assert login_throttle.client_ip(req({})) == "172.18.0.5"


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
