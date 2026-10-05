import asyncio
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select, update

from app.api.v1 import mfa as mfa_mod
from app.core import login_throttle, security, totp
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.crypto import encrypt

PW = "Old-Password-12345"
LOGIN = "/api/v1/auth/login/access-token"
LOGIN2 = "/api/v1/auth/login/mfa"


class _Throttle:
    def __init__(self, retry=None):
        self.retry = retry

    async def reserve(self, key, ip):
        return self.retry

    async def reset(self, key, ip):
        return None


class _Redis:
    def __init__(self):
        self.keys = set()

    async def set(self, key, val, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


async def _setup():
    """ta (plain), tb (require_mfa). Users: plain(ta), mfa(ta, MFA on), forced(tb), multi(ta+tb), sa (no membership)."""
    h = secrets.token_hex(3)
    secret = totp.new_secret()
    async with AsyncSessionLocal() as db:
        ta = Tenant(name="wf-test", slug=f"wf-test-{h}-la")
        tb = Tenant(name="wf-test", slug=f"wf-test-{h}-lb", require_mfa=True)
        db.add_all([ta, tb])
        await db.flush()
        users = {}
        for name, tenants, sa in [("plain", [ta], False), ("mfa", [ta], False), ("forced", [tb], False),
                                  ("multi", [ta, tb], False), ("sa", [], True)]:
            u = User(email=f"wf-test-{h}-{name}@example.com", hashed_password=get_password_hash(PW),
                     is_active=True, is_super_admin=sa)
            if name == "mfa":
                u.mfa_secret_enc = encrypt(secret)
                u.mfa_enabled_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            db.add(u)
            await db.flush()
            for t in tenants:
                db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
            users[name] = u
        await db.commit()
        ids = {"ta": int(ta.id), "tb": int(tb.id), **{k: int(v.id) for k, v in users.items()},
               "emails": {k: v.email for k, v in users.items()}, "secret": secret}
    return ids


async def _cleanup(ids):
    uids = [ids[k] for k in ("plain", "mfa", "forced", "multi", "sa")]
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([ids["ta"], ids["tb"]])))
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(uids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(Tenant).where(Tenant.id.in_([ids["ta"], ids["tb"]])))
        await db.commit()


def _scenario(body, mfa_retry=None, pass_redis=False):
    async def go():
        await engine.dispose()
        ids = await _setup()
        try:
            redis = _Redis()
            app.dependency_overrides[login_throttle.get_login_throttle] = lambda: _Throttle()
            app.dependency_overrides[login_throttle.get_pwd_throttle] = lambda: _Throttle()
            app.dependency_overrides[mfa_mod.get_redis] = lambda: redis
            app.dependency_overrides[mfa_mod.get_mfa_throttle] = lambda: _Throttle(mfa_retry)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await (body(c, ids, redis) if pass_redis else body(c, ids))
        finally:
            for d in (login_throttle.get_login_throttle, login_throttle.get_pwd_throttle, mfa_mod.get_redis,
                      mfa_mod.get_mfa_throttle):
                app.dependency_overrides.pop(d, None)
            await _cleanup(ids)
            await engine.dispose()
    asyncio.run(go())


async def _pw(c, ids, who):
    return await c.post(LOGIN, data={"username": ids["emails"][who], "password": PW})


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


def test_login_normal():
    async def body(c, ids):
        r = await _pw(c, ids, "plain")
        assert r.status_code == 200 and set(r.json()) == {"access_token", "token_type"}
        assert (await c.get("/api/v1/users/me", headers=_bearer(r.json()["access_token"]))).status_code == 200
    _scenario(body)


def test_login_mfa_required_then_code():
    async def body(c, ids):
        r = await _pw(c, ids, "mfa")
        assert r.status_code == 200 and r.json() == {"mfa_required": True, "mfa_token": r.json()["mfa_token"]}
        assert (await c.get("/api/v1/users/me", headers=_bearer(r.json()["mfa_token"]))).status_code == 401
        code = totp.totp_at(ids["secret"], time.time())
        r2 = await c.post(LOGIN2, json={"mfa_token": r.json()["mfa_token"], "code": code})
        assert r2.status_code == 200, r2.text
        assert set(r2.json()) == {"access_token", "token_type"}
        me = await c.get("/api/v1/users/me", headers=_bearer(r2.json()["access_token"]))
        assert me.status_code == 200 and me.json()["active_tenant_id"] == ids["ta"]
    _scenario(body)


def test_wrong_code_400():
    async def body(c, ids):
        tok = (await _pw(c, ids, "mfa")).json()["mfa_token"]
        r = await c.post(LOGIN2, json={"mfa_token": tok, "code": "000000"})
        assert r.status_code == 400
    _scenario(body)


def test_code_replay_rejected():
    async def body(c, ids):
        tok = (await _pw(c, ids, "mfa")).json()["mfa_token"]
        code = totp.totp_at(ids["secret"], time.time())
        assert (await c.post(LOGIN2, json={"mfa_token": tok, "code": code})).status_code == 200
        assert (await c.post(LOGIN2, json={"mfa_token": tok, "code": code})).status_code == 400
    _scenario(body)


def test_mfa_throttle_429():
    async def body(c, ids):
        tok = (await _pw(c, ids, "mfa")).json()["mfa_token"]
        r = await c.post(LOGIN2, json={"mfa_token": tok, "code": "123456"})
        assert r.status_code == 429 and r.headers["Retry-After"] == "30"
    _scenario(body, mfa_retry=30)


def test_expired_challenge_401():
    async def body(c, ids):
        tok = security.create_access_token(
            {"sub": ids["emails"]["mfa"], "purpose": "mfa", "tv": 0}, expires_delta=timedelta(seconds=-1))
        r = await c.post(LOGIN2, json={"mfa_token": tok, "code": "123456"})
        assert r.status_code == 401 and r.json()["detail"] == "Login expired — sign in again"
    _scenario(body)


def test_access_token_not_a_challenge():
    async def body(c, ids):
        at = (await _pw(c, ids, "plain")).json()["access_token"]
        r = await c.post(LOGIN2, json={"mfa_token": at, "code": "123456"})
        assert r.status_code == 401
    _scenario(body)


def test_challenge_stale_inactive_or_mfa_disabled_401():
    async def body(c, ids):
        tok = (await _pw(c, ids, "mfa")).json()["mfa_token"]
        code = totp.totp_at(ids["secret"], time.time())
        # token_version bump invalidates
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == ids["mfa"]).values(token_version=User.token_version + 1))
            await db.commit()
        assert (await c.post(LOGIN2, json={"mfa_token": tok, "code": code})).status_code == 401
        # challenge for a user without MFA enabled is not a login step
        pt = security.issue_mfa_challenge(type("U", (), {"email": ids["emails"]["plain"], "token_version": 0})())
        assert (await c.post(LOGIN2, json={"mfa_token": pt, "code": code})).status_code == 401
        # inactive
        tok2 = security.issue_mfa_challenge(type("U", (), {"email": ids["emails"]["mfa"], "token_version": 1})())
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == ids["mfa"]).values(is_active=False))
            await db.commit()
        assert (await c.post(LOGIN2, json={"mfa_token": tok2, "code": code})).status_code == 401
    _scenario(body)


def test_setup_required_branch():
    async def body(c, ids):
        r = await _pw(c, ids, "forced")
        j = r.json()
        assert r.status_code == 200 and j["mfa_setup_required"] is True and "access_token" not in j
        ch = _bearer(j["mfa_token"])
        assert (await c.get("/api/v1/users/me", headers=ch)).status_code == 401
        s = await c.post("/api/v1/users/me/mfa/setup", headers=ch)
        assert s.status_code == 200, s.text
        code = totp.totp_at(s.json()["secret"], time.time())
        e = await c.post("/api/v1/users/me/mfa/enable", headers=ch, json={"code": code})
        assert e.status_code == 200, e.text
        me = await c.get("/api/v1/users/me", headers=_bearer(e.json()["access_token"]))
        assert me.status_code == 200 and me.json()["mfa_enabled"] is True
    _scenario(body)


def test_switch_tenant_requires_mfa():
    async def body(c, ids):
        at = (await _pw(c, ids, "multi")).json()
        # multi has a require_mfa tenant -> forced setup at login; use a direct token instead
        assert at["mfa_setup_required"] is True
        async with AsyncSessionLocal() as db:
            u = (await db.execute(__import__("sqlalchemy").select(User).where(User.id == ids["multi"]))).scalars().one()
            tok = security.issue_access_token(u, ids["ta"])
        r = await c.post("/api/v1/auth/switch-tenant", headers=_bearer(tok), json={"tenant_id": ids["tb"]})
        assert r.status_code == 403 and r.json()["detail"] == "mfa_setup_required"
        ok = await c.post("/api/v1/auth/switch-tenant", headers=_bearer(tok), json={"tenant_id": ids["ta"]})
        assert ok.status_code == 200 and set(ok.json()) == {"access_token", "token_type"}
    _scenario(body)


# Super admins are NOT exempt from tenant MFA requirements (Ruling M4).
async def _token(uid, tenant_id):
    async with AsyncSessionLocal() as db:
        u = (await db.execute(select(User).where(User.id == uid))).scalars().one()
        return security.issue_access_token(u, tenant_id)


async def _give_mfa(uid, secret):
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == uid).values(
            mfa_secret_enc=encrypt(secret), mfa_enabled_at=datetime.now(timezone.utc)))
        await db.commit()


def test_super_admin_login_forced_setup_when_any_tenant_requires_mfa():
    async def body(c, ids):
        r = await _pw(c, ids, "sa")  # no memberships; tenant tb requires MFA
        j = r.json()
        assert r.status_code == 200 and j["mfa_setup_required"] is True and "access_token" not in j
    _scenario(body)


def test_super_admin_switch_tenant_checks_target_requirement():
    async def body(c, ids):
        tok = await _token(ids["sa"], ids["ta"])
        r = await c.post("/api/v1/auth/switch-tenant", headers=_bearer(tok), json={"tenant_id": ids["tb"]})
        assert r.status_code == 403 and r.json()["detail"] == "mfa_setup_required"
        ok = await c.post("/api/v1/auth/switch-tenant", headers=_bearer(tok), json={"tenant_id": ids["ta"]})
        assert ok.status_code == 200
        await _give_mfa(ids["sa"], ids["secret"])
        r = await c.post("/api/v1/auth/switch-tenant", headers=_bearer(tok), json={"tenant_id": ids["tb"]})
        assert r.status_code == 200
    _scenario(body)


def test_super_admin_tenant_override_requires_mfa():
    async def body(c, ids):
        hdr = _bearer(await _token(ids["sa"], ids["ta"]))
        url = "/api/v1/tenants/current/security"
        r = await c.get(url, headers=hdr, params={"tenant_id": ids["tb"]})
        assert r.status_code == 403 and r.json()["detail"] == "mfa_setup_required"
        assert (await c.get(url, headers=hdr, params={"tenant_id": ids["ta"]})).status_code == 200
        assert (await c.get(url, headers=hdr)).status_code == 200
        await _give_mfa(ids["sa"], ids["secret"])
        r = await c.get(url, headers=hdr, params={"tenant_id": ids["tb"]})
        assert r.status_code == 200 and r.json()["require_mfa"] is True
    _scenario(body)


def test_super_admin_me_and_disable_consider_all_tenants():
    async def body(c, ids):
        hdr = _bearer(await _token(ids["sa"], ids["ta"]))
        me = await c.get("/api/v1/users/me", headers=hdr)
        assert me.status_code == 200 and me.json()["mfa_required_by_tenant"] is True
        await _give_mfa(ids["sa"], ids["secret"])
        r = await c.post("/api/v1/users/me/mfa/disable", headers=hdr,
                         json={"current_password": PW, "code": totp.totp_at(ids["secret"], time.time())})
        assert r.status_code == 403
        assert r.json()["detail"] == "Your organization requires two-factor authentication"
    _scenario(body)


def test_challenge_is_single_use():
    async def body(c, ids):
        tok = (await _pw(c, ids, "mfa")).json()["mfa_token"]
        now = time.time()
        assert (await c.post(LOGIN2, json={"mfa_token": tok, "code": totp.totp_at(ids["secret"], now)})).status_code == 200
        r = await c.post(LOGIN2, json={"mfa_token": tok, "code": totp.totp_at(ids["secret"], now + 30)})
        assert r.status_code == 401 and r.json()["detail"] == "Login expired — sign in again"
    _scenario(body)


def test_challenge_without_jti_rejected():
    async def body(c, ids):
        tok = security.create_access_token({"sub": ids["emails"]["mfa"], "purpose": "mfa", "tv": 0},
                                           expires_delta=timedelta(minutes=5))
        r = await c.post(LOGIN2, json={"mfa_token": tok, "code": totp.totp_at(ids["secret"], time.time())})
        assert r.status_code == 401
    _scenario(body)


def test_forced_enable_spent_challenge_rejected():
    async def body(c, ids, redis):
        tok = (await _pw(c, ids, "forced")).json()["mfa_token"]
        ch = _bearer(tok)
        secret = (await c.post("/api/v1/users/me/mfa/setup", headers=ch)).json()["secret"]
        redis.keys.add(f"mfa:chal:{security.decode_mfa_challenge(tok)['jti']}")  # already used
        r = await c.post("/api/v1/users/me/mfa/enable", headers=ch,
                         json={"code": totp.totp_at(secret, time.time())})
        assert r.status_code == 401 and r.json()["detail"] == "Login expired — sign in again"
        async with AsyncSessionLocal() as db:
            u = (await db.execute(select(User).where(User.id == ids["forced"]))).scalars().one()
        assert u.mfa_enabled_at is None
    _scenario(body, pass_redis=True)


def test_mfa_login_code_max_length():
    async def body(c, ids):
        tok = (await _pw(c, ids, "mfa")).json()["mfa_token"]
        assert (await c.post(LOGIN2, json={"mfa_token": tok, "code": "1" * 17})).status_code == 422
    _scenario(body)


def test_login_rejects_non_email_username_before_reserving():
    calls = []

    class Rec(_Throttle):
        async def reserve(self, key, ip):
            calls.append(key)
            return None

    async def body(c, ids):
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: Rec()
        for name in (f"mfa:{ids['mfa']}", f"pwd:{ids['mfa']}", "admin", "a b@example.com"):
            r = await c.post(LOGIN, data={"username": name, "password": "x"})
            assert r.status_code == 401 and r.json()["detail"] == "Incorrect email or password"
        assert calls == []
        assert (await _pw(c, ids, "plain")).status_code == 200 and len(calls) == 1
    _scenario(body)


def test_throttle_namespaces_never_collide():
    """5 failed logins with username mfa:<id> must not lock that user's MFA codes (Ruling S2)."""
    from tests.test_login_throttle import FakeRedis

    assert mfa_mod.get_mfa_throttle().prefix == "mfa"
    assert login_throttle.get_pwd_throttle().prefix == "pwd"
    assert login_throttle.get_login_throttle().prefix == "login"
    fr = FakeRedis(time.monotonic)
    base = login_throttle.LoginThrottle(fr)
    mfa_t = base.namespaced("mfa", max_email_ip=5, max_email=5)

    async def body(c, ids):
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: base
        app.dependency_overrides[mfa_mod.get_mfa_throttle] = lambda: mfa_t
        for _ in range(5):
            for name in (f"mfa:{ids['mfa']}", f"mfa:{ids['mfa']}@example.com", str(ids["mfa"])):
                r = await c.post(LOGIN, data={"username": name, "password": "wrong-password"})
                assert r.status_code == 401
        assert not any(k.startswith("mfa:") for k in fr.data)
        tok = (await _pw(c, ids, "mfa")).json()["mfa_token"]
        r = await c.post(LOGIN2, json={"mfa_token": tok, "code": totp.totp_at(ids["secret"], time.time())})
        assert r.status_code == 200, r.text
        # MFA failures land only under mfa:attempts:<user id>
        await mfa_t.reserve(str(ids["mfa"]), "203.0.113.9")
        assert fr._live(f"mfa:attempts:email:{ids['mfa']}") is not None
        assert fr._live(f"login:attempts:email:{ids['mfa']}") is None
    _scenario(body)
