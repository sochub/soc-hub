import asyncio
import secrets

import httpx
from sqlalchemy import delete, select

from app.core import login_throttle, security
from app.core.security import get_password_hash, verify_password
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User

OLD_PW = "Old-Password-12345"
NEW_PW = "New-Password-67890"


class _FakeThrottle:
    async def reserve(self, key, ip):
        return None

    async def reset(self, key, ip):
        return None


async def _setup(password_login=True):
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{h}-pf")
        u = User(email=f"wf-test-{h}-pf@example.com", hashed_password=get_password_hash(OLD_PW),
                 is_active=True, password_login_enabled=password_login)
        db.add_all([t, u])
        await db.flush()
        db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
        await db.commit()
        return {"t": int(t.id), "u": int(u.id)}


async def _cleanup(ids):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuditLog).where(AuditLog.entity_type == "user", AuditLog.entity_id == ids["u"]))
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id == ids["u"]))
        await db.execute(delete(User).where(User.id == ids["u"]))
        await db.execute(delete(Tenant).where(Tenant.id == ids["t"]))
        await db.commit()


async def _user(uid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.id == uid))).scalars().one()


def _scenario(body, **kw):
    async def go():
        await engine.dispose()
        ids = await _setup(**kw)
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: _FakeThrottle()
        try:
            u = await _user(ids["u"])
            hdr = {"Authorization": f"Bearer {security.issue_access_token(u, ids['t'])}"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await body(c, ids, hdr)
        finally:
            app.dependency_overrides.pop(login_throttle.get_login_throttle, None)
            await _cleanup(ids)
            await engine.dispose()
    asyncio.run(go())


def test_update_profile_fields():
    async def body(c, ids, hdr):
        r = await c.put("/api/v1/users/me", headers=hdr, json={
            "full_name": "  Ada Lovelace ", "job_title": "Analyst", "timezone": "Europe/Madrid"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert (j["full_name"], j["job_title"], j["timezone"]) == ("Ada Lovelace", "Analyst", "Europe/Madrid")
        assert j["has_password"] is True and j["mfa_enabled"] is False and j["has_avatar"] is False
        r = await c.put("/api/v1/users/me", headers=hdr, json={"timezone": "Mars/Base"})
        assert r.status_code == 422
        r = await c.put("/api/v1/users/me", headers=hdr, json={"job_title": "x" * 101})
        assert r.status_code == 422
        r = await c.put("/api/v1/users/me", headers=hdr, json={"full_name": "   "})
        assert r.status_code == 422
    _scenario(body)


def test_password_change_requires_current():
    async def body(c, ids, hdr):
        r = await c.post("/api/v1/users/me/password", headers=hdr,
                         json={"current_password": "wrong-password-1A", "new_password": NEW_PW})
        assert r.status_code == 400 and r.json()["detail"] == "Current password is incorrect"
        r = await c.post("/api/v1/users/me/password", headers=hdr,
                         json={"current_password": OLD_PW, "new_password": "weak"})
        assert r.status_code == 422
        assert verify_password(OLD_PW, (await _user(ids["u"])).hashed_password)
    _scenario(body)


def test_password_change_revokes_old_token():
    async def body(c, ids, hdr):
        r = await c.post("/api/v1/users/me/password", headers=hdr,
                         json={"current_password": OLD_PW, "new_password": NEW_PW})
        assert r.status_code == 200, r.text
        assert r.json()["token_type"] == "bearer"
        assert (await c.get("/api/v1/users/me", headers=hdr)).status_code == 401
        new_hdr = {"Authorization": f"Bearer {r.json()['access_token']}"}
        assert (await c.get("/api/v1/users/me", headers=new_hdr)).status_code == 200
        assert verify_password(NEW_PW, (await _user(ids["u"])).hashed_password)
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(AuditLog).where(
                AuditLog.entity_type == "user", AuditLog.entity_id == ids["u"],
                AuditLog.action == "password_changed"))).scalars().all()
        assert len(rows) == 1
        assert OLD_PW not in str(rows[0].changes) and NEW_PW not in str(rows[0].changes)
    _scenario(body)


def test_put_me_ignores_password():
    async def body(c, ids, hdr):
        before = (await _user(ids["u"])).hashed_password
        r = await c.put("/api/v1/users/me", headers=hdr, json={"full_name": "Zed", "password": NEW_PW})
        assert r.status_code in (200, 422)
        assert (await _user(ids["u"])).hashed_password == before
    _scenario(body)


def test_sso_only_user_cannot_change_password():
    async def body(c, ids, hdr):
        me = await c.get("/api/v1/users/me", headers=hdr)
        assert me.json()["has_password"] is False
        r = await c.post("/api/v1/users/me/password", headers=hdr,
                         json={"current_password": OLD_PW, "new_password": NEW_PW})
        assert r.status_code == 400
        assert verify_password(OLD_PW, (await _user(ids["u"])).hashed_password)
    _scenario(body, password_login=False)


def test_password_change_is_throttled():
    class Blocked(_FakeThrottle):
        async def reserve(self, key, ip):
            return 30

    async def body(c, ids, hdr):
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: Blocked()
        r = await c.post("/api/v1/users/me/password", headers=hdr,
                         json={"current_password": OLD_PW, "new_password": NEW_PW})
        assert r.status_code == 429 and r.headers["retry-after"] == "30"
    _scenario(body)


def test_me_reports_mfa_required_by_tenant():
    async def body(c, ids, hdr):
        r = await c.get("/api/v1/users/me", headers=hdr)
        assert r.json()["mfa_required_by_tenant"] is False
        async with AsyncSessionLocal() as db:
            t = (await db.execute(select(Tenant).where(Tenant.id == ids["t"]))).scalars().one()
            t.require_mfa = True
            await db.commit()
        r = await c.get("/api/v1/users/me", headers=hdr)
        assert r.json()["mfa_required_by_tenant"] is True
    _scenario(body)


def test_me_avatar_version_is_opaque_stem():
    async def body(c, ids, hdr):
        r = await c.get("/api/v1/users/me", headers=hdr)
        assert r.json()["avatar_version"] is None
        async with AsyncSessionLocal() as db:
            u = (await db.execute(select(User).where(User.id == ids["u"]))).scalars().one()
            u.avatar_key = f"avatars/{ids['u']}/abcdef0123456789abcdef.webp"
            await db.commit()
        r = await c.get("/api/v1/users/me", headers=hdr)
        v = r.json()["avatar_version"]
        assert v == "abcdef0123456789" and "avatars" not in v
    _scenario(body)
