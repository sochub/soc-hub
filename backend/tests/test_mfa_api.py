from datetime import datetime, timezone
import asyncio
import secrets
import time

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


class _FakeThrottle:
    async def reserve(self, key, ip):
        return None

    async def reset(self, key, ip):
        return None


class _FakeRedis:
    def __init__(self):
        self.keys = set()

    async def set(self, key, val, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


async def _setup():
    """tenant A (admin, analyst, member), tenant B (admin_b), super admin."""
    h = secrets.token_hex(3)
    ids = {}
    async with AsyncSessionLocal() as db:
        ta = Tenant(name="wf-test", slug=f"wf-test-{h}-ma")
        tb = Tenant(name="wf-test", slug=f"wf-test-{h}-mb")
        db.add_all([ta, tb])
        await db.flush()
        users = {}
        for name, tenant, role, sa in [("admin", ta, "admin", False), ("analyst", ta, "analyst", False),
                                       ("member", ta, "analyst", False), ("admin_b", tb, "admin", False),
                                       ("sa", None, None, True)]:
            u = User(email=f"wf-test-{h}-{name}@example.com", hashed_password=get_password_hash(PW),
                     is_active=True, is_super_admin=sa)
            db.add(u)
            await db.flush()
            if tenant:
                db.add(TenantMembership(user_id=u.id, tenant_id=tenant.id, role=role))
            users[name] = u
        await db.commit()
        ids = {"ta": int(ta.id), "tb": int(tb.id), **{k: int(v.id) for k, v in users.items()}}
    return ids


async def _cleanup(ids):
    uids = [v for k, v in ids.items() if k not in ("ta", "tb")]
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([ids["ta"], ids["tb"]])))
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(uids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(Tenant).where(Tenant.id.in_([ids["ta"], ids["tb"]])))
        await db.commit()


async def _user(uid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.id == uid))).scalars().one()


async def _hdr(uid, tenant):
    u = await _user(uid)
    return {"Authorization": f"Bearer {security.issue_access_token(u, tenant)}"}


def _scenario(body):
    async def go():
        await engine.dispose()
        ids = await _setup()
        redis = _FakeRedis()
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: _FakeThrottle()
        app.dependency_overrides[mfa_mod.get_redis] = lambda: redis
        app.dependency_overrides[mfa_mod.get_mfa_throttle] = lambda: _FakeThrottle()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await body(c, ids)
        finally:
            app.dependency_overrides.pop(login_throttle.get_login_throttle, None)
            app.dependency_overrides.pop(mfa_mod.get_redis, None)
            app.dependency_overrides.pop(mfa_mod.get_mfa_throttle, None)
            await _cleanup(ids)
            await engine.dispose()
    asyncio.run(go())


async def _enable(c, ids, who="member"):
    hdr = await _hdr(ids[who], ids["ta"])
    r = await c.post("/api/v1/users/me/mfa/setup", headers=hdr)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    code = totp.totp_at(secret, time.time())
    r2 = await c.post("/api/v1/users/me/mfa/enable", headers=hdr, json={"code": code})
    return hdr, secret, code, r, r2


def test_setup_enable_flow():
    async def body(c, ids):
        hdr, secret, code, r1, r2 = await _enable(c, ids)
        assert r1.json()["otpauth_uri"].startswith("otpauth://totp/")
        assert r2.status_code == 200, r2.text
        assert secret not in r2.text and code not in r2.text
        assert (await c.get("/api/v1/users/me", headers=hdr)).status_code == 401
        new = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        me = await c.get("/api/v1/users/me", headers=new)
        assert me.status_code == 200 and me.json()["mfa_enabled"] is True
        assert secret not in me.text
        assert (await c.post("/api/v1/users/me/mfa/setup", headers=new)).status_code == 409
        # replay of the same code is rejected
        again = await c.post("/api/v1/users/me/mfa/disable", headers=new,
                             json={"current_password": PW, "code": code})
        assert again.status_code == 400 and again.json()["detail"] == "Invalid or expired code"
        assert (await _user(ids["member"])).mfa_enabled_at is not None
    _scenario(body)


def test_enable_via_challenge_token():
    async def body(c, ids):
        u = await _user(ids["member"])
        ch = {"Authorization": f"Bearer {security.issue_mfa_challenge(u)}"}
        r = await c.post("/api/v1/users/me/mfa/setup", headers=ch)
        assert r.status_code == 200, r.text
        code = totp.totp_at(r.json()["secret"], time.time())
        r2 = await c.post("/api/v1/users/me/mfa/enable", headers=ch, json={"code": code})
        assert r2.status_code == 200, r2.text
        new = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        me = await c.get("/api/v1/users/me", headers=new)
        assert me.status_code == 200 and me.json()["active_tenant_id"] == ids["ta"]
        # challenge no longer valid (MFA enabled / version bumped); challenge can't be a normal token
        assert (await c.post("/api/v1/users/me/mfa/setup", headers=ch)).status_code == 401
        assert (await c.get("/api/v1/users/me", headers=ch)).status_code == 401
    _scenario(body)


def test_enable_wrong_code():
    async def body(c, ids):
        hdr = await _hdr(ids["member"], ids["ta"])
        await c.post("/api/v1/users/me/mfa/setup", headers=hdr)
        r = await c.post("/api/v1/users/me/mfa/enable", headers=hdr, json={"code": "000000"})
        assert r.status_code == 400 and r.json()["detail"] == "Invalid or expired code"
        assert (await _user(ids["member"])).mfa_enabled_at is None
        r = await c.post("/api/v1/users/me/mfa/enable", headers=await _hdr(ids["analyst"], ids["ta"]),
                         json={"code": "123456"})
        assert r.status_code == 400  # no pending setup
    _scenario(body)


def test_setup_when_enabled_409():
    async def body(c, ids):
        hdr, *_ , r2 = await _enable(c, ids)
        assert r2.status_code == 200
        new = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        assert (await c.post("/api/v1/users/me/mfa/setup", headers=new)).status_code == 409
    _scenario(body)


def test_disable_requires_password_and_code():
    async def body(c, ids):
        _, secret, code, _, r2 = await _enable(c, ids)
        new = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        fresh = totp.totp_at(secret, time.time() + 30)
        r = await c.post("/api/v1/users/me/mfa/disable", headers=new,
                         json={"current_password": "wrong-password-1A", "code": fresh})
        assert r.status_code == 400 and r.json()["detail"] == "Current password is incorrect"
        r = await c.post("/api/v1/users/me/mfa/disable", headers=new,
                         json={"current_password": PW, "code": "000000"})
        assert r.status_code == 400 and r.json()["detail"] == "Invalid or expired code"
        r = await c.post("/api/v1/users/me/mfa/disable", headers=new,
                         json={"current_password": PW, "code": fresh})
        assert r.status_code == 200, r.text
        u = await _user(ids["member"])
        assert u.mfa_enabled_at is None and u.mfa_secret_enc is None
        assert (await c.get("/api/v1/users/me", headers=new)).status_code == 401
        post = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert (await c.get("/api/v1/users/me", headers=post)).json()["mfa_enabled"] is False
    _scenario(body)


def test_disable_blocked_when_tenant_requires():
    async def body(c, ids):
        _, secret, _, _, r2 = await _enable(c, ids)
        new = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        async with AsyncSessionLocal() as db:
            await db.execute(update(Tenant).where(Tenant.id == ids["ta"]).values(require_mfa=True))
            await db.commit()
        r = await c.post("/api/v1/users/me/mfa/disable", headers=new,
                         json={"current_password": PW, "code": totp.totp_at(secret, time.time() + 30)})
        assert r.status_code == 403
        assert r.json()["detail"] == "Your organization requires two-factor authentication"
        assert (await _user(ids["member"])).mfa_enabled_at is not None
    _scenario(body)


def test_admin_reset_revokes_target():
    async def body(c, ids):
        async def enable_member():
            async with AsyncSessionLocal() as db:
                await db.execute(update(User).where(User.id == ids["member"]).values(
                    mfa_secret_enc=encrypt("JBSWY3DPEHPK3PXP"), mfa_enabled_at=datetime.now(timezone.utc)))
                await db.commit()
        await enable_member()
        url = f"/api/v1/users/{ids['member']}/mfa/reset"
        old = await _hdr(ids["member"], ids["ta"])
        assert (await c.post(url, headers=await _hdr(ids["admin_b"], ids["tb"]))).status_code == 404
        assert (await c.post(url, headers=await _hdr(ids["analyst"], ids["ta"]))).status_code == 404
        assert (await c.post(url, headers=await _hdr(ids["admin"], ids["ta"]))).status_code == 204
        u = await _user(ids["member"])
        assert u.mfa_enabled_at is None and u.mfa_secret_enc is None
        assert (await c.get("/api/v1/users/me", headers=old)).status_code == 401
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(AuditLog).where(
                AuditLog.entity_id == ids["member"], AuditLog.action == "mfa_reset"))).scalars().all()
        assert len(rows) == 1 and rows[0].user_id == ids["admin"]
        await enable_member()
        assert (await c.post(url, headers=await _hdr(ids["sa"], ids["ta"]))).status_code == 204
        assert (await _user(ids["member"])).mfa_enabled_at is None
        assert (await c.post("/api/v1/users/99999999/mfa/reset",
                             headers=await _hdr(ids["sa"], ids["ta"]))).status_code == 404
    _scenario(body)


def test_tenant_security_toggle():
    async def body(c, ids):
        adm = await _hdr(ids["admin"], ids["ta"])
        r = await c.get("/api/v1/tenants/current/security", headers=adm)
        assert r.status_code == 200 and r.json() == {"require_mfa": False, "members_without_mfa": 3}
        r = await c.put("/api/v1/tenants/current/security", headers=adm, json={"require_mfa": True})
        assert r.status_code == 200 and r.json()["require_mfa"] is True
        assert (await c.get("/api/v1/tenants/current/security", headers=adm)).json()["require_mfa"] is True
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(AuditLog).where(
                AuditLog.entity_type == "tenant", AuditLog.entity_id == ids["ta"],
                AuditLog.action == "require_mfa_changed"))).scalars().all()
        assert len(rows) == 1
        ana = await _hdr(ids["analyst"], ids["ta"])
        assert (await c.get("/api/v1/tenants/current/security", headers=ana)).status_code == 403
        assert (await c.put("/api/v1/tenants/current/security", headers=ana,
                            json={"require_mfa": False})).status_code == 403
    _scenario(body)


def test_undecryptable_secret_409():
    async def body(c, ids):
        hdr = await _hdr(ids["member"], ids["ta"])
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == ids["member"]).values(mfa_secret_enc="garbage"))
            await db.commit()
        r = await c.post("/api/v1/users/me/mfa/enable", headers=hdr, json={"code": "123456"})
        assert r.status_code == 409 and r.json()["detail"] == "MFA unavailable — contact your admin"
    _scenario(body)


def test_throttle_429():
    class Blocked(_FakeThrottle):
        async def reserve(self, key, ip):
            return 42

    async def body(c, ids):
        hdr = await _hdr(ids["member"], ids["ta"])
        await c.post("/api/v1/users/me/mfa/setup", headers=hdr)
        app.dependency_overrides[mfa_mod.get_mfa_throttle] = lambda: Blocked()
        r = await c.post("/api/v1/users/me/mfa/enable", headers=hdr, json={"code": "123456"})
        assert r.status_code == 429 and r.headers["retry-after"] == "42"
    _scenario(body)


def test_challenge_after_enable_rejected():
    async def body(c, ids):
        await _enable(c, ids)
        u = await _user(ids["member"])  # token_version already bumped; challenge is current
        ch = {"Authorization": f"Bearer {security.issue_mfa_challenge(u)}"}
        assert u.mfa_enabled_at is not None
        assert (await c.post("/api/v1/users/me/mfa/setup", headers=ch)).status_code == 401
        assert (await c.post("/api/v1/users/me/mfa/enable", headers=ch, json={"code": "123456"})).status_code == 401
    _scenario(body)


def test_reset_protections_and_no_secret_leak():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id.in_([ids["sa"], ids["admin"]])).values(
                mfa_secret_enc=encrypt("JBSWY3DPEHPK3PXP"), mfa_enabled_at=datetime.now(timezone.utc)))
            await db.execute(update(User).where(User.id == ids["sa"]).values(is_super_admin=True))
            # super admin also a member of tenant A, so a tenant admin could otherwise reach them
            db.add(TenantMembership(user_id=ids["sa"], tenant_id=ids["ta"], role="viewer"))
            await db.commit()
        adm = await _hdr(ids["admin"], ids["ta"])
        r = await c.post(f"/api/v1/users/{ids['sa']}/mfa/reset", headers=adm)
        assert r.status_code == 404
        assert (await _user(ids["sa"])).mfa_enabled_at is not None
        r = await c.post(f"/api/v1/users/{ids['admin']}/mfa/reset", headers=adm)
        assert r.status_code == 400 and r.json()["detail"] == "Use Turn off two-factor in your profile"
        assert (await _user(ids["admin"])).mfa_enabled_at is not None
        sa = await _hdr(ids["sa"], ids["ta"])
        r = await c.post(f"/api/v1/users/{ids['admin']}/mfa/reset", headers=sa)
        assert r.status_code == 204 and r.text == ""
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(AuditLog).where(
                AuditLog.entity_id == ids["admin"], AuditLog.action == "mfa_reset"))).scalars().one()
        assert row.tenant_id == ids["ta"]
    _scenario(body)


def test_disable_sso_only_and_responses_hide_secret():
    async def body(c, ids):
        _, secret, _, _, r2 = await _enable(c, ids)
        new = {"Authorization": f"Bearer {r2.json()['access_token']}"}
        r = await c.post("/api/v1/users/me/mfa/disable", headers=new,
                         json={"current_password": PW, "code": totp.totp_at(secret, time.time() + 30)})
        assert r.status_code == 200 and secret not in r.text
        r = await c.get("/api/v1/tenants/current/security", headers=await _hdr(ids["admin"], ids["ta"]))
        assert secret not in r.text
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == ids["analyst"]).values(
                password_login_enabled=False, mfa_secret_enc=encrypt("JBSWY3DPEHPK3PXP"),
                mfa_enabled_at=datetime.now(timezone.utc)))
            await db.commit()
        r = await c.post("/api/v1/users/me/mfa/disable", headers=await _hdr(ids["analyst"], ids["ta"]),
                         json={"current_password": "x", "code": "123456"})
        assert r.status_code == 400
        assert r.json()["detail"] == "Two-factor for SSO-only accounts can only be reset by an admin"
    _scenario(body)
