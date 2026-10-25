"""Member login method enforcement: tokens carry amr/sso_tid, every request re-checks the active
tenant's member_login_method, login/switch-tenant pick only policy-allowed tenants."""
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie

import jwt
from sqlalchemy import delete, select

from app.api.v1 import mfa as mfa_mod
from app.api.v1 import sso as sso_mod
from app.core import login_throttle, security, totp
from app.core.config import settings
from app.core.security import get_password_hash
from app.core.session_cookie import COOKIE
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.utils.crypto import encrypt
from tests.audit_fixtures import _FakeThrottle
from tests.guest_fixtures import FakeAsyncRedis, client, make_tenant, run
from tests.test_guest_sso import FakeAuth

PW = "Method-Passw0rd!x"
LOGIN = "/api/v1/auth/login/access-token"
XRW = {"X-Requested-With": "XMLHttpRequest"}


def _overrides():
    redis = FakeAsyncRedis()
    app.dependency_overrides[login_throttle.get_login_throttle] = lambda: _FakeThrottle()
    app.dependency_overrides[login_throttle.get_pwd_throttle] = lambda: _FakeThrottle()
    app.dependency_overrides[mfa_mod.get_mfa_throttle] = lambda: _FakeThrottle()
    app.dependency_overrides[mfa_mod.get_redis] = lambda: redis


async def _tenant(db, ids, method="password", sso=False) -> Tenant:
    t = await make_tenant(db, ids, sso=sso)
    t.member_login_method = method
    await db.commit()
    return t


async def _user(db, ids, name, memberships, *, mfa_secret=None, password_login=True) -> User:
    u = User(email=f"g-{ids.h}-{name}@example.com", hashed_password=get_password_hash(PW), is_active=True,
             password_login_enabled=password_login)
    if mfa_secret:
        u.mfa_secret_enc = encrypt(mfa_secret)
        u.mfa_enabled_at = datetime.now(timezone.utc)
    db.add(u)
    await db.flush()
    ids.users.append(int(u.id))
    db.add_all([TenantMembership(user_id=u.id, tenant_id=t.id, role=role) for t, role in memberships])
    await db.commit()
    return u


async def _drop_memberships(ids):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(ids.users or [-1])))
        await db.commit()


def _go(body):
    async def wrapped(ids):
        _overrides()
        try:
            async with client() as c:
                await body(c, ids)
        finally:
            await _drop_memberships(ids)
    run(wrapped)


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


def _claims(tok):
    return jwt.decode(tok, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


async def _pw(c, email):
    return await c.post(LOGIN, data={"username": email, "password": PW})


async def _fallback_rows(tid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.action == "sso.password_fallback"))).scalars().all()


def _sso_required(r, slug):
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "sso_required", "slug": slug}


def _session_cookie(r) -> str:
    c = SimpleCookie()
    c.load(r.headers["set-cookie"])
    return c[COOKIE].value


async def _saml_login(c, monkeypatch, tenant, email) -> str:
    monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(email))
    r = await c.post(f"/api/v1/auth/saml/{tenant.slug}/acs", data={"SAMLResponse": "x"})
    assert r.status_code == 303 and r.headers["location"].endswith("/login#sso=ok"), r.headers["location"]
    return _session_cookie(r)


def test_analyst_password_refused_at_saml_tenant():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "saml")
            u = await _user(db, ids, "an", [(t, "analyst")])
        r = await _pw(c, u.email)
        _sso_required(r, t.slug)
    _go(body)


def test_admin_fallback_without_mfa_enrolls_first():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "saml")
            u = await _user(db, ids, "adm", [(t, "admin")])
        r = await _pw(c, u.email)
        j = r.json()
        assert r.status_code == 200 and j.get("mfa_setup_required") is True and "access_token" not in j, r.text
        ch = _bearer(j["mfa_token"])
        s = await c.post("/api/v1/users/me/mfa/setup", headers=ch)
        assert s.status_code == 200, s.text
        e = await c.post("/api/v1/users/me/mfa/enable", headers=ch,
                         json={"code": totp.totp_at(s.json()["secret"], time.time())})
        assert e.status_code == 200, e.text
        tok = e.json()["access_token"]
        assert _claims(tok)["amr"] == "pwd"
        me = await c.get("/api/v1/users/me", headers=_bearer(tok))
        assert me.status_code == 200 and me.json()["active_tenant_id"] == t.id
        assert len(await _fallback_rows(t.id)) == 1
    _go(body)


def test_admin_fallback_with_mfa():
    async def body(c, ids):
        secret = totp.new_secret()
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "saml")
            u = await _user(db, ids, "adm", [(t, "admin")], mfa_secret=secret)
        r = await _pw(c, u.email)
        assert r.status_code == 200 and r.json().get("mfa_required") is True, r.text
        r2 = await c.post("/api/v1/auth/login/mfa", json={"mfa_token": r.json()["mfa_token"],
                                                          "code": totp.totp_at(secret, time.time())})
        assert r2.status_code == 200, r2.text
        me = await c.get("/api/v1/users/me", headers=_bearer(r2.json()["access_token"]))
        assert me.status_code == 200 and me.json()["active_tenant_id"] == t.id
        assert len(await _fallback_rows(t.id)) == 1
    _go(body)


def test_analyst_at_sso_tenant_refused_before_mfa():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "oidc")
            u = await _user(db, ids, "an", [(t, "analyst")], mfa_secret=totp.new_secret())
        r = await _pw(c, u.email)
        _sso_required(r, t.slug)
        assert "mfa_token" not in r.text
    _go(body)


def test_sso_user_keeps_amr_after_mfa_enable():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "saml", sso=True)
            u = await _user(db, ids, "sso", [(t, "analyst")], password_login=False)
            tok = security.issue_access_token(u, t.id, amr="saml", sso_tenant_id=t.id)
        s = await c.post("/api/v1/users/me/mfa/setup", headers=_bearer(tok))
        assert s.status_code == 200, s.text
        e = await c.post("/api/v1/users/me/mfa/enable", headers=_bearer(tok),
                         json={"code": totp.totp_at(s.json()["secret"], time.time())})
        assert e.status_code == 200, e.text
        new = e.json()["access_token"]
        assert _claims(new)["amr"] == "saml" and _claims(new)["sso_tid"] == t.id
        assert (await c.get("/api/v1/users/me", headers=_bearer(new))).status_code == 200
    _go(body)


def test_switch_into_sso_tenant_refused():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            ta = await _tenant(db, ids, "password")
            tb = await _tenant(db, ids, "oidc")
            u = await _user(db, ids, "multi", [(ta, "analyst"), (tb, "analyst")])
        r = await _pw(c, u.email)
        assert r.status_code == 200, r.text
        tok = r.json()["access_token"]
        assert (await c.get("/api/v1/users/me", headers=_bearer(tok))).json()["active_tenant_id"] == ta.id
        sw = await c.post("/api/v1/auth/switch-tenant", headers=_bearer(tok), json={"tenant_id": tb.id})
        _sso_required(sw, tb.slug)
    _go(body)


def test_multi_tenant_user_sso_into_b(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            ta = await _tenant(db, ids, "password")
            tb = await _tenant(db, ids, "saml", sso=True)
            u = await _user(db, ids, "multi", [(ta, "analyst"), (tb, "analyst")])
        sess = await _saml_login(c, monkeypatch, tb, u.email)
        c.cookies.clear()
        hdr = {"Cookie": f"{COOKIE}={sess}"}
        me = await c.get("/api/v1/users/me", headers=hdr)
        assert me.status_code == 200 and me.json()["active_tenant_id"] == tb.id
        sw = await c.post("/api/v1/auth/switch-tenant", headers={**hdr, **XRW}, json={"tenant_id": ta.id})
        assert sw.status_code == 403 and sw.json()["detail"] == {"code": "password_required"}
        async with AsyncSessionLocal() as db:  # no membership was added
            n = (await db.execute(select(TenantMembership.tenant_id).where(
                TenantMembership.user_id == u.id))).scalars().all()
        assert sorted(n) == sorted([ta.id, tb.id])
    _go(body)


def test_method_change_cuts_existing_sessions():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "password")
            u = await _user(db, ids, "an", [(t, "analyst")])
        tok = (await _pw(c, u.email)).json()["access_token"]
        assert (await c.get("/api/v1/users/me", headers=_bearer(tok))).status_code == 200
        async with AsyncSessionLocal() as db:
            (await db.get(Tenant, t.id)).member_login_method = "saml"
            await db.commit()
        _sso_required(await c.get("/api/v1/users/me", headers=_bearer(tok)), t.slug)
    _go(body)


def test_legacy_token_without_amr_is_pwd():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "saml")
            u = await _user(db, ids, "an", [(t, "analyst")])
        tok = security.create_access_token({"sub": u.email, "active_tenant_id": t.id, "tv": 0})
        _sso_required(await c.get("/api/v1/users/me", headers=_bearer(tok)), t.slug)
    _go(body)


def test_saml_session_works(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "saml", sso=True)
            u = await _user(db, ids, "an", [(t, "analyst")])
        sess = await _saml_login(c, monkeypatch, t, u.email)
        assert _claims(sess)["amr"] == "saml" and _claims(sess)["sso_tid"] == t.id
        c.cookies.clear()
        me = await c.get("/api/v1/users/me", headers={"Cookie": f"{COOKIE}={sess}"})
        assert me.status_code == 200 and me.json()["active_tenant_id"] == t.id
    _go(body)
