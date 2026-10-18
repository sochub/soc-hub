from tests.audit_fixtures import PW, hdr, rows, run


async def _login(c, email, pw):
    return await c.post("/api/v1/auth/login/access-token", data={"username": email, "password": pw})


def test_login_success_and_failures():
    async def body(c, w):
        assert (await _login(c, w.emails["admin"], PW)).status_code == 200
        ok = await rows(w, action="login.success")
        assert len(ok) == 1 and ok[0].tenant_id == w.ta and ok[0].user_id == w.users["admin"]
        assert ok[0].category == "security" and ok[0].outcome == "success" and ok[0].status_code == 200

        assert (await _login(c, w.emails["admin"], "wrong-password!!")).status_code == 401
        bad = await rows(w, action="login.failed", tenant_id=w.ta)
        assert len(bad) == 1 and bad[0].changes == {"email_domain": "example.com"} and bad[0].user_id is None

        assert (await _login(c, f"nobody@{w.h}.invalid", "CANARYPW-123456")).status_code == 401
        unknown = await rows(w, action="login.failed")
        plat = [r for r in unknown if r.tenant_id is None]
        assert len(plat) == 1 and plat[0].changes == {"email_domain": f"{w.h}.invalid"}
        for r in await rows(w):
            assert "CANARYPW" not in repr(vars(r)) and "wrong-password" not in repr(vars(r))
        # a login is one row, not an extra "write" row
        assert await rows(w, category="write", route="/api/v1/auth/login/access-token") == []
    run(body)


def test_switch_tenant_and_logout():
    async def body(c, w):
        r = await c.post("/api/v1/auth/switch-tenant", json={"tenant_id": w.tb}, headers=hdr(w, "root"))
        assert r.status_code == 200
        sw = await rows(w, action="tenant.switch")
        assert len(sw) == 1 and sw[0].tenant_id == w.tb and sw[0].changes == {"from_tenant_id": w.ta}
        assert (await c.post("/api/v1/auth/logout", headers=hdr(w, "admin"))).status_code == 204
        lo = await rows(w, action="logout")
        assert len(lo) == 1 and lo[0].tenant_id == w.ta and lo[0].user_id == w.users["admin"]
    run(body)


def test_password_change_event():
    async def body(c, w):
        r = await c.post("/api/v1/users/me/password", json={"current_password": PW, "new_password": "Brand-New-Passw0rd!"},
                         headers=hdr(w, "analyst"))
        assert r.status_code == 200, r.text
        ev = await rows(w, action="password.changed")
        assert len(ev) == 1 and ev[0].user_id == w.users["analyst"]
        for row in await rows(w):
            assert "Brand-New-Passw0rd" not in repr(vars(row)) and PW not in repr(vars(row))
    run(body)


def test_locked_login_row_has_429():
    from app.core import login_throttle
    from app.main import app

    class Locked:
        async def reserve(self, key, ip):
            return 60

        async def reset(self, key, ip):
            return None

    async def body(c, w):
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: Locked()
        assert (await _login(c, w.emails["admin"], PW)).status_code == 429
        lk = await rows(w, action="login.locked")
        assert len(lk) == 1 and lk[0].status_code == 429 and lk[0].outcome == "denied"
        assert lk[0].tenant_id == w.ta and lk[0].changes == {"email_domain": "example.com"}
    run(body)


def test_malformed_domain_never_stored_on_failed_or_locked_login():
    from app.core import login_throttle
    from app.main import app

    class Locked:
        async def reserve(self, key, ip):
            return 60

        async def reset(self, key, ip):
            return None

    async def body(c, w):
        bad = f"nobody@{w.h}<script>.invalid"  # passes the email shape check, fails the domain check
        assert (await _login(c, bad, "whatever-123456")).status_code == 401
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: Locked()
        assert (await _login(c, bad, "whatever-123456")).status_code == 429
        got = await rows(w, action="login.failed") + await rows(w, action="login.locked")
        assert len(got) == 2 and all(r.changes == {"email_domain": None} for r in got)
    run(body)


class _Redis:
    def __init__(self):
        self.keys = set()

    async def set(self, key, val, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def _mfa_overrides():
    from app.api.v1 import mfa as mfa_mod
    from app.main import app
    from tests.audit_fixtures import _FakeThrottle
    redis = _Redis()
    app.dependency_overrides[mfa_mod.get_redis] = lambda: redis
    app.dependency_overrides[mfa_mod.get_mfa_throttle] = lambda: _FakeThrottle()


def test_mfa_login_events_and_login_success():
    import time
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.core import totp
    from app.db.session import AsyncSessionLocal
    from app.models.user import User
    from app.utils.crypto import encrypt

    async def body(c, w):
        _mfa_overrides()
        secret = totp.new_secret()
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == w.users["admin"]).values(
                mfa_secret_enc=encrypt(secret), mfa_enabled_at=datetime.now(timezone.utc)))
            await db.commit()
        r = await _login(c, w.emails["admin"], PW)
        assert r.status_code == 200 and r.json()["mfa_required"] is True
        tok = r.json()["mfa_token"]
        req = await rows(w, action="login.mfa_required")
        assert len(req) == 1 and req[0].tenant_id == w.ta and req[0].user_id == w.users["admin"]
        assert await rows(w, action="login.success") == []

        bad = await c.post("/api/v1/auth/login/mfa", json={"mfa_token": tok, "code": "000000"})
        assert bad.status_code == 400
        failed = await rows(w, action="mfa.challenge_failed")
        assert len(failed) == 1 and failed[0].outcome == "denied" and failed[0].status_code == 400
        assert failed[0].tenant_id == w.ta

        ok = await c.post("/api/v1/auth/login/mfa", json={"mfa_token": tok,
                                                          "code": totp.totp_at(secret, time.time())})
        assert ok.status_code == 200, ok.text
        passed = await rows(w, action="mfa.challenge_passed")
        success = await rows(w, action="login.success")
        assert len(passed) == 1 and len(success) == 1
        s = success[0]
        assert (s.tenant_id, s.user_id, s.user_email, s.outcome, s.status_code) == (
            w.ta, w.users["admin"], w.emails["admin"], "success", 200)
        assert s.request_id == passed[0].request_id
    run(body)


def test_mfa_setup_at_login_records_login_success():
    import time

    from sqlalchemy import update

    from app.core import totp
    from app.db.session import AsyncSessionLocal
    from app.models.tenant import Tenant

    async def body(c, w):
        _mfa_overrides()
        async with AsyncSessionLocal() as db:
            await db.execute(update(Tenant).where(Tenant.id == w.tb).values(require_mfa=True))
            await db.commit()
        r = await _login(c, w.emails["admin_b"], PW)
        assert r.status_code == 200 and r.json()["mfa_setup_required"] is True
        ch = {"Authorization": f"Bearer {r.json()['mfa_token']}"}
        s = await c.post("/api/v1/users/me/mfa/setup", headers=ch)
        assert s.status_code == 200, s.text
        e = await c.post("/api/v1/users/me/mfa/enable", headers=ch,
                         json={"code": totp.totp_at(s.json()["secret"], time.time())})
        assert e.status_code == 200, e.text
        success = await rows(w, action="login.success")
        assert len(success) == 1 and success[0].tenant_id == w.tb and success[0].user_id == w.users["admin_b"]
        assert len(await rows(w, action="mfa.enabled")) == 1
    run(body)
