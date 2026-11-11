"""A tenant admin may not take over the lifecycle of accounts it doesn't own.

- Super admins are break-glass: only a super admin can invite, deactivate, reactivate, reset MFA on, change the
  role of, or remove a super admin.
- Global account actions (deactivate / activate / MFA reset) are allowed to a tenant admin only for a user whose
  HOME tenant (oldest membership) is the admin's active tenant. Any other tenant manages the membership only
  (role, remove).
- An existing account joins a tenant that already has members only by accepting the invitation signed in.
- Every refused attempt writes a security audit row."""
import asyncio
import secrets
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select, update

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.api.v1 import invitations as invitations_mod
from app.main import app
from app.models.audit_log import AuditLog
from app.models.invitation import Invitation, InvitationStatus
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.crypto import encrypt
from tests.session_fixtures import make_web_token

OTHER_TENANTS = "This account belongs to other tenants; ask a platform admin."


async def _setup():
    """tenant A: admin, admin2, single (A only), multi (A + B, home A), sa (super admin, viewer in A);
    tenant B: admin_b, multi; tenant C: empty; sa2: super admin with no membership."""
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        ta = Tenant(name="wf-test", slug=f"wf-test-{h}-ga")
        tb = Tenant(name="wf-test", slug=f"wf-test-{h}-gb")
        tc = Tenant(name="wf-test", slug=f"wf-test-{h}-gc")
        db.add_all([ta, tb, tc])
        await db.flush()
        users = {}
        spec = [("admin", [(ta, "admin")], False), ("admin2", [(ta, "admin")], False),
                ("single", [(ta, "analyst")], False), ("multi", [(ta, "analyst"), (tb, "analyst")], False),
                ("sa", [(ta, "viewer")], True), ("sa2", [], True), ("admin_b", [(tb, "admin")], False)]
        for name, ms, sa in spec:
            u = User(email=f"wf-test-{h}-{name}@example.com", hashed_password=get_password_hash(secrets.token_hex(8)),
                     is_active=True, is_super_admin=sa, password_login_enabled=True)
            db.add(u)
            await db.flush()
            for t, role in ms:
                db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role=role))
                await db.flush()  # memberships keep their order: the first one listed is the home tenant
            users[name] = u
        await db.commit()
        return {"ta": int(ta.id), "tb": int(tb.id), "tc": int(tc.id), **{k: int(v.id) for k, v in users.items()}}


async def _cleanup(ids):
    uids = [v for k, v in ids.items() if k not in ("ta", "tb", "tc")]
    tids = [ids["ta"], ids["tb"], ids["tc"]]
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
        await db.execute(delete(Invitation).where(Invitation.tenant_id.in_(tids)))
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
            AuditLog.tenant_id.in_([ids["ta"], ids["tb"], ids["tc"]])))).scalars().all()


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
        # same answer as another existing account that can't be added (reveals nothing about the account)
        assert r.status_code == 409 and r.json()["detail"] == invitations_mod.CANNOT_ADD_EXISTING, r.text
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


def test_tenant_admin_global_actions_refused_outside_home_tenant():
    """N1: B (not multi's home) can't change account-wide state. Previously this was "refused for any
    multi-tenant user"; now it is the home-tenant rule."""
    async def body(c, ids):
        await _enable_mfa(ids["multi"])
        adm = await _hdr(ids["admin_b"], ids["tb"])
        m = ids["multi"]
        for method, url in [("put", f"/api/v1/users/{m}/deactivate"), ("put", f"/api/v1/users/{m}/activate"),
                            ("post", f"/api/v1/users/{m}/mfa/reset")]:
            r = await getattr(c, method)(url, headers=adm)
            assert r.status_code == 403 and r.json()["detail"] == OTHER_TENANTS, (url, r.text)
        u = await _user(m)
        assert u.is_active and u.mfa_enabled_at is not None
        rows = await _denials(ids)
        assert len(rows) == 3 and all(x.entity_id == m and x.user_id == ids["admin_b"] for x in rows)
        # membership-scoped actions still work
        r = await c.put(f"/api/v1/users/{m}/role", headers=adm, json={"role": "viewer"})
        assert r.status_code == 200 and r.json()["role"] == "viewer", r.text
        r = await c.delete(f"/api/v1/users/{m}/membership", headers=adm)
        assert r.status_code == 204, r.text
        assert await _membership(m, ids["tb"]) is None
        assert await _membership(m, ids["ta"]) is not None
    _scenario(body)


def test_home_tenant_admin_manages_multi_tenant_user():
    """N1: the admin of the user's home (oldest) tenant keeps account-wide rights after another tenant adds them."""
    async def body(c, ids):
        await _enable_mfa(ids["multi"])
        adm = await _hdr(ids["admin"], ids["ta"])
        m = ids["multi"]
        r = await c.put(f"/api/v1/users/{m}/deactivate", headers=adm)
        assert r.status_code == 200 and r.json()["is_active"] is False, r.text
        r = await c.put(f"/api/v1/users/{m}/activate", headers=adm)
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/v1/users/{m}/mfa/reset", headers=adm)
        assert r.status_code == 204, r.text
        assert await _denials(ids) == []
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
        assert flags[ids["multi"]] is True and flags[ids["sa"]] is False  # A is multi's home tenant
        r = await c.get("/api/v1/users/", headers=await _hdr(ids["admin_b"], ids["tb"]))
        assert {u["id"]: u["account_manageable"] for u in r.json()}[ids["multi"]] is False
        r = await c.get("/api/v1/users/", headers=await _hdr(ids["sa"], ids["ta"]))
        assert all(u["account_manageable"] for u in r.json())
    _scenario(body)


async def _invitation(tid, email):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Invitation).where(
            Invitation.tenant_id == tid, Invitation.email == email))).scalars().first()


def test_existing_user_must_accept_invitation_signed_in(monkeypatch):
    """N1: an existing account is no longer force-added to a tenant that has members (this replaces the old
    "existing user added directly" test): it gets a pending invitation, and only that account, signed in,
    can accept it."""
    async def body(c, ids):
        sent = []

        async def fake_send(email, link, tenant_id, db):
            sent.append(email)
        monkeypatch.setattr(invitations_mod, "send_invitation_email", fake_send)
        single = await _user(ids["single"])
        r = await c.post("/api/v1/invitations/", headers=await _hdr(ids["admin_b"], ids["tb"]),
                         json={"email": single.email, "role": "admin"})
        assert r.status_code == 201 and r.json()["added_directly"] is False and r.json()["token"], r.text
        assert await _membership(ids["single"], ids["tb"]) is None and sent == [single.email]
        token = r.json()["token"]
        v = (await c.get(f"/api/v1/invitations/validate/{token}")).json()
        assert v["valid"] and v["existing_account"] is True
        # the public signup path can't claim it, nor can another signed-in account
        r = await c.post("/api/v1/invitations/accept",
                         json={"token": token, "full_name": "x", "password": "A-Strong-Passw0rd!x"})
        assert r.status_code == 409, r.text
        r = await c.post("/api/v1/invitations/accept-existing", headers=await _hdr(ids["admin2"], ids["ta"]),
                         json={"token": token})
        assert r.status_code == 403, r.text
        assert await _membership(ids["single"], ids["tb"]) is None
        # the invited account accepts
        r = await c.post("/api/v1/invitations/accept-existing", headers=await _hdr(ids["single"], ids["ta"]),
                         json={"token": token})
        assert r.status_code == 200, r.text
        m = await _membership(ids["single"], ids["tb"])
        assert m is not None and m.role == "admin"
        assert (await _invitation(ids["tb"], single.email)).status == InvitationStatus.ACCEPTED
        r = await c.post("/api/v1/invitations/accept-existing", headers=await _hdr(ids["single"], ids["ta"]),
                         json={"token": token})
        assert r.status_code == 400  # single use
        # A stays the home tenant: B still can't deactivate the account
        r = await c.put(f"/api/v1/users/{ids['single']}/deactivate", headers=await _hdr(ids["admin_b"], ids["tb"]))
        assert r.status_code == 403 and r.json()["detail"] == OTHER_TENANTS
    _scenario(body)


def test_existing_user_added_directly_to_empty_tenant():
    """Bootstrap: a tenant with no members yet may get its first (admin) account added directly."""
    async def body(c, ids):
        single = await _user(ids["single"])
        r = await c.post("/api/v1/invitations/", headers=await _hdr(ids["sa2"], ids["tc"]),
                         json={"email": single.email, "role": "admin"})
        assert r.status_code == 201 and r.json()["added_directly"] is True, r.text
        m = await _membership(ids["single"], ids["tc"])
        assert m is not None and m.role == "admin"
    _scenario(body)
