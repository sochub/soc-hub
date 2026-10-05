"""SSO account rules (Ruling S1): a tenant's IdP may only sign in accounts that
belong to that tenant alone; auto-provision only creates NEW users."""
import asyncio
import secrets
from urllib.parse import unquote

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api.v1 import sso as sso_mod
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user import User

PW = "Old-Password-12345"


async def _setup():
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        ta = Tenant(name="wf-test", slug=f"wf-test-{h}-sa")
        tb = Tenant(name="wf-test", slug=f"wf-test-{h}-sb")
        db.add_all([ta, tb])
        await db.flush()
        db.add(TenantSSOConfig(tenant_id=ta.id, enabled=True, idp_entity_id="https://idp.example.com",
                               idp_sso_url="https://idp.example.com/sso", idp_x509_cert="MIIB",
                               auto_provision=True, default_role="analyst"))
        users = {}
        for name, tenants, sa in [("local", [ta], False), ("multi", [ta, tb], False),
                                  ("other", [tb], False), ("sa", [], True), ("sa_member", [ta], True)]:
            u = User(email=f"wf-test-{h}-{name}@example.com", hashed_password=get_password_hash(PW),
                     is_active=True, is_super_admin=sa)
            db.add(u)
            await db.flush()
            for t in tenants:
                db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
            users[name] = u
        await db.commit()
        return {"h": h, "ta": int(ta.id), "tb": int(tb.id), "slug": ta.slug,
                **{k: int(v.id) for k, v in users.items()}, "emails": {k: v.email for k, v in users.items()},
                "extra_users": []}


async def _cleanup(ids):
    uids = [ids[k] for k in ("local", "multi", "other", "sa", "sa_member")] + ids["extra_users"]
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([ids["ta"], ids["tb"]])))
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(uids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(TenantSSOConfig).where(TenantSSOConfig.tenant_id == ids["ta"]))
        await db.execute(delete(Tenant).where(Tenant.id.in_([ids["ta"], ids["tb"]])))
        await db.commit()


def _run(body):
    async def go():
        await engine.dispose()
        ids = await _setup()
        try:
            await body(ids)
        finally:
            await _cleanup(ids)
            await engine.dispose()
    asyncio.run(go())


async def _resolve(ids, email):
    async with AsyncSessionLocal() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.id == ids["ta"]))).scalars().one()
        cfg = (await db.execute(select(TenantSSOConfig).where(
            TenantSSOConfig.tenant_id == ids["ta"]))).scalars().one()
        return await sso_mod._resolve_sso_user(db, tenant, cfg, email, "Someone")


async def _memberships(uid):
    async with AsyncSessionLocal() as db:
        return sorted((await db.execute(select(TenantMembership.tenant_id).where(
            TenantMembership.user_id == uid))).scalars().all())


async def _refused(ids, who):
    try:
        await _resolve(ids, ids["emails"][who])
    except HTTPException as exc:
        assert exc.status_code == 403 and exc.detail == sso_mod.SSO_NOT_ALLOWED
        return
    raise AssertionError(f"{who} was allowed to sign in through SSO")


def test_single_tenant_member_allowed():
    async def body(ids):
        u = await _resolve(ids, ids["emails"]["local"].upper())
        assert u.id == ids["local"]
    _run(body)


def test_super_admins_refused():
    async def body(ids):
        await _refused(ids, "sa")
        await _refused(ids, "sa_member")
        assert await _memberships(ids["sa"]) == []
    _run(body)


def test_member_of_another_tenant_refused():
    async def body(ids):
        await _refused(ids, "multi")
    _run(body)


def test_existing_non_member_not_auto_attached():
    async def body(ids):
        await _refused(ids, "other")
        assert await _memberships(ids["other"]) == [ids["tb"]]
    _run(body)


def test_auto_provision_creates_new_user_only():
    async def body(ids):
        email = f"wf-test-{ids['h']}-new@example.com"
        u = await _resolve(ids, email)
        ids["extra_users"].append(int(u.id))
        assert u.password_login_enabled is False and not u.is_super_admin
        assert await _memberships(u.id) == [ids["ta"]]
    _run(body)


def test_acs_redirects_with_clear_message(monkeypatch):
    class FakeAuth:
        def __init__(self, email):
            self.email = email

        def process_response(self):
            pass

        def get_errors(self):
            return []

        def is_authenticated(self):
            return True

        def get_nameid(self):
            return self.email

        def get_attributes(self):
            return {}

    async def body(ids):
        current = {}
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(current["e"]))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            url = f"/api/v1/auth/saml/{ids['slug']}/acs"
            for who in ("sa", "multi", "other"):
                current["e"] = ids["emails"][who]
                r = await c.post(url, data={"SAMLResponse": "x"})
                assert r.status_code == 303
                loc = r.headers["location"]
                assert "#sso_error=" in loc and "sso_token" not in loc
                assert unquote(loc.split("#sso_error=", 1)[1]) == sso_mod.SSO_NOT_ALLOWED
            current["e"] = ids["emails"]["local"]
            r = await c.post(url, data={"SAMLResponse": "x"})
            assert r.status_code == 303 and "#sso_token=" in r.headers["location"]
    _run(body)
