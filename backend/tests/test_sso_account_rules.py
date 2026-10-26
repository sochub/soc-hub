"""SSO account rules: a tenant's IdP may only sign in existing members of that tenant alone (not members
of other tenants too: the tenant's admins control the IdP); never super admins; auto-provision only
creates NEW users."""
import asyncio
import secrets
import uuid
from urllib.parse import unquote

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api.v1 import sso as sso_mod
from app.core import session_cookie
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
        ta = Tenant(name="wf-test", slug=f"wf-test-{h}-sa", member_login_method="saml")
        tb = Tenant(name="wf-test", slug=f"wf-test-{h}-sb")
        db.add_all([ta, tb])
        await db.flush()
        db.add(TenantSSOConfig(tenant_id=ta.id, idp_entity_id="https://idp.example.com",
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



def _signed_in(r) -> bool:
    """SSO success: session cookie set on the redirect, and no token anywhere in the URL."""
    loc = r.headers["location"]
    return loc.endswith("/login#sso=ok") and r.headers.get("set-cookie", "").startswith(f"{session_cookie.COOKIE}=")

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
        assert await _memberships(ids["multi"]) == sorted([ids["ta"], ids["tb"]])  # unchanged
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
            self.aid = f"_{uuid.uuid4().hex}"

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

        def get_last_assertion_id(self):
            return self.aid

        def get_last_assertion_not_on_or_after(self):
            return None

    async def body(ids):
        current = {}
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(current["e"]))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            url = f"/api/v1/auth/saml/{ids['slug']}/acs"
            for who in ("sa", "other", "multi"):
                current["e"] = ids["emails"][who]
                r = await c.post(url, data={"SAMLResponse": "x"})
                assert r.status_code == 303
                loc = r.headers["location"]
                assert "#sso_error=" in loc and "set-cookie" not in r.headers
                assert unquote(loc.split("#sso_error=", 1)[1]) == sso_mod.SSO_NOT_ALLOWED
            for who in ("local",):
                current["e"] = ids["emails"][who]
                r = await c.post(url, data={"SAMLResponse": "x"})
                assert r.status_code == 303 and _signed_in(r)
        async with AsyncSessionLocal() as db:
            failed = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == ids["ta"], AuditLog.action == "sso.failed"))).scalars().all()
            ok = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == ids["ta"], AuditLog.action == "sso.success"))).scalars().all()
        assert len(failed) == 3 and all(r.category == "security" and r.outcome == "denied" for r in failed)
        assert {r.changes["reason"] for r in failed} == {"not_allowed"}
        assert sso_mod.SSO_NOT_ALLOWED not in repr([vars(r) for r in failed + ok])
        assert [r.user_id for r in ok] == [ids["local"]]
    _run(body)


def test_saml_invalid_stores_fixed_code_not_idp_text(monkeypatch):
    secret_msg = "StatusMessage for victim@leak.example"

    class BadAuth:
        def process_response(self):
            pass

        def get_errors(self):
            return ["invalid_response"]

        def is_authenticated(self):
            return False

        def get_last_error_reason(self):
            return secret_msg

    async def body(ids):
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: BadAuth())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/v1/auth/saml/{ids['slug']}/acs", data={"SAMLResponse": "x"})
        assert r.status_code == 303
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == ids["ta"]))).scalars().all()
        failed = [r for r in rows if r.action == "sso.failed"]
        assert len(failed) == 1 and failed[0].changes == {"reason": "saml_invalid"}
        assert "leak.example" not in repr([vars(r) for r in rows])
    _run(body)


class _FixedAuth:
    """An assertion with a chosen ID / NotOnOrAfter, for replay checks."""
    def __init__(self, email, aid, not_after=None):
        self.email, self.aid, self.not_after = email, aid, not_after

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

    def get_last_assertion_id(self):
        return self.aid

    def get_last_assertion_not_on_or_after(self):
        return self.not_after


def _sso_error(r):
    loc = r.headers["location"]
    assert r.status_code == 303 and "#sso_error=" in loc and "set-cookie" not in r.headers, loc
    return unquote(loc.split("#sso_error=", 1)[1])


def test_acs_rejects_replayed_assertion(monkeypatch):
    async def body(ids):
        cur = {"aid": "_replay-1", "na": None}
        monkeypatch.setattr(sso_mod.saml_service, "make_auth",
                            lambda slug, cfg, req: _FixedAuth(ids["emails"]["local"], cur["aid"], cur["na"]))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            url = f"/api/v1/auth/saml/{ids['slug']}/acs"
            r = await c.post(url, data={"SAMLResponse": "x"})
            assert r.status_code == 303 and _signed_in(r)
            assert _sso_error(await c.post(url, data={"SAMLResponse": "x"})) == sso_mod.SSO_FAILED
            cur["aid"] = None  # an assertion without an ID is refused too
            assert _sso_error(await c.post(url, data={"SAMLResponse": "x"})) == sso_mod.SSO_FAILED
            cur["aid"], cur["na"] = "_fresh-2", int(__import__("time").time()) + 300
            r = await c.post(url, data={"SAMLResponse": "x"})
            assert _signed_in(r)
        keys = sso_mod._saml_redis().keys
        assert keys[f"saml_aid:{ids['ta']}:_replay-1"] == 86400  # no NotOnOrAfter -> no library time bound -> max TTL
        assert 655 <= keys[f"saml_aid:{ids['ta']}:_fresh-2"] <= 660  # NotOnOrAfter - now + clock drift (300s) + 60s
    _run(body)


def test_assertion_ttl_clamped():
    import time

    class _R:
        async def set(self, k, v, nx=False, ex=None):
            self.ex = ex
            return True

    async def go():
        r = _R()
        orig = sso_mod._saml_redis
        sso_mod._saml_redis = lambda: r
        try:
            assert await sso_mod._claim_assertion(1, _FixedAuth("a@b", "_a", int(time.time()) - 3600))
            assert r.ex == 360
            assert await sso_mod._claim_assertion(1, _FixedAuth("a@b", "_b", int(time.time()) + 10 * 86400))
            assert r.ex == 86400
        finally:
            sso_mod._saml_redis = orig
    asyncio.run(go())


def test_acs_fails_closed_without_redis(monkeypatch):
    class _Down:
        async def set(self, *a, **k):
            raise ConnectionError("redis down")

    async def body(ids):
        monkeypatch.setattr(sso_mod, "_saml_redis", lambda: _Down())
        monkeypatch.setattr(sso_mod.saml_service, "make_auth",
                            lambda slug, cfg, req: _FixedAuth(ids["emails"]["local"], "_down-1"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(f"/api/v1/auth/saml/{ids['slug']}/acs", data={"SAMLResponse": "x"})
            assert _sso_error(r) == sso_mod.SSO_FAILED
    _run(body)
