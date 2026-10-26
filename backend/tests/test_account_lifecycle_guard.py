"""A tenant admin may not take over the lifecycle of accounts it doesn't own.

- Super admins are break-glass: only a super admin can invite, deactivate, reactivate, reset MFA on, change the
  role of, or remove a super admin.
- Global account actions (deactivate / activate / MFA reset) are allowed to a tenant admin only for a user whose
  sole membership is the admin's active tenant. For a user who also belongs elsewhere the tenant admin manages the
  membership only (role, remove).
- Every refused attempt writes a security audit row."""
import asyncio
import secrets
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select, update

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.crypto import encrypt
from tests.session_fixtures import make_web_token

OTHER_TENANTS = "This account belongs to other tenants; ask a platform admin."


async def _setup():
    """tenant A: admin, admin2, single (A only), multi (A + B), sa (super admin, viewer in A);
    tenant B: multi; sa2: super admin with no membership."""
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        ta = Tenant(name="wf-test", slug=f"wf-test-{h}-ga")
        tb = Tenant(name="wf-test", slug=f"wf-test-{h}-gb")
        db.add_all([ta, tb])
        await db.flush()
        users = {}
        spec = [("admin", [(ta, "admin")], False), ("admin2", [(ta, "admin")], False),
                ("single", [(ta, "analyst")], False), ("multi", [(ta, "analyst"), (tb, "analyst")], False),
                ("sa", [(ta, "viewer")], True), ("sa2", [], True)]
        for name, ms, sa in spec:
            u = User(email=f"wf-test-{h}-{name}@example.com", hashed_password=get_password_hash(secrets.token_hex(8)),
                     is_active=True, is_super_admin=sa, password_login_enabled=True)
            db.add(u)
            await db.flush()
            for t, role in ms:
                db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role=role))
            users[name] = u
        await db.commit()
        return {"ta": int(ta.id), "tb": int(tb.id), **{k: int(v.id) for k, v in users.items()}}


async def _cleanup(ids):
    uids = [v for k, v in ids.items() if k not in ("ta", "tb")]
    tids = [ids["ta"], ids["tb"]]
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(uids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
        await db.commit()


async def _user(uid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.id == uid))).scalars().one()


async def _hdr(uid, tenant):
    return {"Authorization": f"Bearer {await make_web_token(None, await _user(uid), tenant)}"}


async def _enable_mfa(uid):
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == uid).values(
            mfa_secret_enc=encrypt("JBSWY3DPEHPK3PXP"), mfa_enabled_at=datetime.now(timezone.utc)))
        await db.commit()


async def _denials(ids):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(AuditLog).where(
            AuditLog.category == "security", AuditLog.action == "user.manage_denied",
            AuditLog.tenant_id.in_([ids["ta"], ids["tb"]])))).scalars().all()


async def _membership(uid, tid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(TenantMembership).where(
            TenantMembership.user_id == uid, TenantMembership.tenant_id == tid))).scalars().first()


def _scenario(body):
    async def go():
        await engine.dispose()
        ids = await _setup()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await body(c, ids)
        finally:
            await _cleanup(ids)
            await engine.dispose()
    asyncio.run(go())


def test_tenant_admin_cannot_invite_super_admin():
    async def body(c, ids):
        sa2 = await _user(ids["sa2"])
        r = await c.post("/api/v1/invitations/", headers=await _hdr(ids["admin"], ids["ta"]),
                         json={"email": sa2.email, "role": "viewer"})
        assert r.status_code == 400, r.text
        assert "super" not in r.json()["detail"].lower()
        assert await _membership(ids["sa2"], ids["ta"]) is None
        rows = await _denials(ids)
        assert len(rows) == 1 and rows[0].user_id == ids["admin"] and rows[0].outcome == "denied"
        # a super admin may add another super admin
        r = await c.post("/api/v1/invitations/", headers=await _hdr(ids["sa"], ids["ta"]),
                         json={"email": sa2.email, "role": "viewer"})
        assert r.status_code == 201 and r.json()["added_directly"], r.text
        assert await _membership(ids["sa2"], ids["ta"]) is not None
    _scenario(body)


def test_tenant_admin_cannot_act_on_super_admin():
    async def body(c, ids):
        await _enable_mfa(ids["sa"])
        adm = await _hdr(ids["admin"], ids["ta"])
        sa = ids["sa"]
        for method, url, kw in [
            ("put", f"/api/v1/users/{sa}/deactivate", {}),
            ("put", f"/api/v1/users/{sa}/activate", {}),
            ("post", f"/api/v1/users/{sa}/mfa/reset", {}),
            ("put", f"/api/v1/users/{sa}/role", {"json": {"role": "admin"}}),
            ("delete", f"/api/v1/users/{sa}/membership", {}),
        ]:
            r = await getattr(c, method)(url, headers=adm, **kw)
            assert r.status_code == 403, (url, r.status_code, r.text)
            assert "super" not in r.text.lower()
        u = await _user(sa)
        assert u.is_active and u.mfa_enabled_at is not None
        m = await _membership(sa, ids["ta"])
        assert m is not None and m.role == "viewer"
        assert len(await _denials(ids)) == 5
    _scenario(body)


def test_tenant_admin_global_actions_refused_on_multi_tenant_user():
    async def body(c, ids):
        await _enable_mfa(ids["multi"])
        adm = await _hdr(ids["admin"], ids["ta"])
        m = ids["multi"]
        for method, url in [("put", f"/api/v1/users/{m}/deactivate"), ("put", f"/api/v1/users/{m}/activate"),
                            ("post", f"/api/v1/users/{m}/mfa/reset")]:
            r = await getattr(c, method)(url, headers=adm)
            assert r.status_code == 403 and r.json()["detail"] == OTHER_TENANTS, (url, r.text)
        u = await _user(m)
        assert u.is_active and u.mfa_enabled_at is not None
        rows = await _denials(ids)
        assert len(rows) == 3 and all(x.entity_id == m and x.user_id == ids["admin"] for x in rows)
        # membership-scoped actions still work
        r = await c.put(f"/api/v1/users/{m}/role", headers=adm, json={"role": "viewer"})
        assert r.status_code == 200 and r.json()["role"] == "viewer", r.text
        r = await c.delete(f"/api/v1/users/{m}/membership", headers=adm)
        assert r.status_code == 204, r.text
        assert await _membership(m, ids["ta"]) is None
        assert await _membership(m, ids["tb"]) is not None
    _scenario(body)


def test_tenant_admin_global_actions_allowed_on_single_tenant_user():
    async def body(c, ids):
        await _enable_mfa(ids["single"])
        s = ids["single"]
        r = await c.put(f"/api/v1/users/{s}/deactivate", headers=await _hdr(ids["admin"], ids["ta"]))
        assert r.status_code == 200 and r.json()["is_active"] is False, r.text
        r = await c.put(f"/api/v1/users/{s}/activate", headers=await _hdr(ids["admin"], ids["ta"]))
        assert r.status_code == 200 and r.json()["is_active"] is True, r.text
        r = await c.post(f"/api/v1/users/{s}/mfa/reset", headers=await _hdr(ids["admin"], ids["ta"]))
        assert r.status_code == 204, r.text
        assert (await _user(s)).mfa_enabled_at is None
        # a tenant admin may manage another single-tenant admin of the same tenant
        r = await c.put(f"/api/v1/users/{ids['admin2']}/deactivate", headers=await _hdr(ids["admin"], ids["ta"]))
        assert r.status_code == 200, r.text
        assert await _denials(ids) == []
    _scenario(body)


def test_super_admin_can_do_everything():
    async def body(c, ids):
        m = ids["multi"]
        await _enable_mfa(m)
        r = await c.put(f"/api/v1/users/{m}/deactivate", headers=await _hdr(ids["sa"], ids["ta"]))
        assert r.status_code == 200, r.text
        r = await c.put(f"/api/v1/users/{m}/activate", headers=await _hdr(ids["sa"], ids["ta"]))
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/v1/users/{m}/mfa/reset", headers=await _hdr(ids["sa"], ids["ta"]))
        assert r.status_code == 204, r.text
        # super admin acting on another super admin (sa2 is not a member of A; super admin may still act)
        await _enable_mfa(ids["sa"])
        r = await c.post(f"/api/v1/users/{ids['sa']}/mfa/reset", headers=await _hdr(ids["sa2"], ids["ta"]))
        assert r.status_code == 204, r.text
        r = await c.put(f"/api/v1/users/{ids['sa']}/role", headers=await _hdr(ids["sa2"], ids["ta"]),
                        json={"role": "analyst"})
        assert r.status_code == 200, r.text
        r = await c.put(f"/api/v1/users/{ids['sa']}/deactivate", headers=await _hdr(ids["sa2"], ids["ta"]))
        assert r.status_code == 200, r.text
        r = await c.put(f"/api/v1/users/{ids['sa']}/activate", headers=await _hdr(ids["sa2"], ids["ta"]))
        assert r.status_code == 200, r.text
        assert await _denials(ids) == []
    _scenario(body)


def test_member_list_flags_manageable_accounts():
    async def body(c, ids):
        r = await c.get("/api/v1/users/", headers=await _hdr(ids["admin"], ids["ta"]))
        assert r.status_code == 200, r.text
        flags = {u["id"]: u["account_manageable"] for u in r.json()}
        assert flags[ids["single"]] is True and flags[ids["admin2"]] is True
        assert flags[ids["multi"]] is False and flags[ids["sa"]] is False
        r = await c.get("/api/v1/users/", headers=await _hdr(ids["sa"], ids["ta"]))
        assert all(u["account_manageable"] for u in r.json())
    _scenario(body)
