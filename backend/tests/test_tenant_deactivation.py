"""A deactivated tenant (``DELETE /tenants/{id}``) locks its members out: REST access, password login,
refresh and switch-tenant all refuse it. The super admin is break-glass and keeps full access to it."""
from sqlalchemy import select, update

from app.core.security import get_password_hash
from app.core.session_cookie import COOKIE, REFRESH_COOKIE
from app.db.session import AsyncSessionLocal
from app.models.auth_session import AuthSession
from app.models.tenant import Tenant
from app.models.user import User
from tests.session_fixtures import make_web_token
from tests.test_web_sessions import (
    LOGIN, PW, REFRESH, XRW, _bearer, _call, _claims, _cookies, _go, _spa_login, _tenant, _user,
)

DEACTIVATED = "This tenant is deactivated"


async def _deactivate(tid):
    async with AsyncSessionLocal() as db:
        await db.execute(update(Tenant).where(Tenant.id == tid).values(is_active=False))
        await db.commit()


async def _super_admin(db, ids) -> User:
    u = User(email=f"g-{ids.h}-sa@example.com", hashed_password=get_password_hash(PW), is_active=True,
             is_super_admin=True)
    db.add(u)
    await db.commit()
    ids.users.append(int(u.id))
    return u


def _detail(r):
    try:
        return r.json().get("detail")
    except ValueError:
        return None


def test_member_api_refused_in_deactivated_tenant():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "admin")])
        tok = await make_web_token(None, u, t.id)
        assert (await _call(c, "GET", "/api/v1/cases/", headers=_bearer(tok))).status_code == 200
        await _deactivate(t.id)
        r = await _call(c, "GET", "/api/v1/cases/", headers=_bearer(tok))
        assert r.status_code == 403 and _detail(r) == DEACTIVATED, r.text
        # admin-only routes too (role checks don't bypass the tenant gate)
        r = await _call(c, "GET", "/api/v1/tenants/sso-config", headers=_bearer(tok))
        assert r.status_code == 403, r.text
        # /users/me still answers (so the UI can sign out / switch) but no longer offers the tenant
        me = await _call(c, "GET", "/api/v1/users/me", headers=_bearer(tok))
        assert me.status_code == 200 and me.json()["memberships"] == []
    _go(body)


def test_login_refused_when_only_tenant_is_deactivated():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        await _deactivate(t.id)
        r = await _call(c, "POST", LOGIN, headers=XRW, data={"username": u.email, "password": PW})
        assert r.status_code == 403 and _detail(r) == DEACTIVATED, r.text
        assert COOKIE not in _cookies(r) and "access_token" not in r.text
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(AuthSession).where(AuthSession.user_id == u.id))).first() is None
    _go(body)


def test_login_falls_to_another_active_tenant():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t1 = await _tenant(db, ids)
            t2 = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t1, "analyst"), (t2, "analyst")])
        await _deactivate(t1.id)
        access, _ = await _spa_login(c, u.email)
        assert _claims(access)["active_tenant_id"] == t2.id
        me = await _call(c, "GET", "/api/v1/users/me", headers=_bearer(access))
        assert [m["tenant_id"] for m in me.json()["memberships"]] == [t2.id]
        # and may not switch into the deactivated one
        r = await _call(c, "POST", "/api/v1/auth/switch-tenant", headers=_bearer(access), json={"tenant_id": t1.id})
        assert r.status_code == 403 and _detail(r) == DEACTIVATED, r.text
    _go(body)


def test_refresh_refused_after_deactivation():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        _, refresh = await _spa_login(c, u.email)
        await _deactivate(t.id)
        r = await _call(c, "POST", REFRESH, refresh=refresh, headers=XRW)
        assert r.status_code == 401, r.text
        cleared = _cookies(r)
        assert cleared[COOKIE].value == "" and cleared[REFRESH_COOKIE].value == ""
    _go(body)


def test_super_admin_keeps_full_access_to_deactivated_tenant():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            other = await _tenant(db, ids)
            sa = await _super_admin(db, ids)
        await _deactivate(t.id)
        # password login works regardless of tenant state
        access, refresh = await _spa_login(c, sa.email)
        # ?tenant_id= override onto the deactivated tenant
        r = await _call(c, "GET", f"/api/v1/cases/?tenant_id={t.id}", headers=_bearer(access))
        assert r.status_code == 200, r.text
        # switch into it, then use it with the token alone
        r = await _call(c, "POST", "/api/v1/auth/switch-tenant", headers=_bearer(access), json={"tenant_id": t.id})
        assert r.status_code == 200, r.text
        access2 = _cookies(r)[COOKIE].value
        assert _claims(access2)["active_tenant_id"] == t.id
        assert (await _call(c, "GET", "/api/v1/cases/", headers=_bearer(access2))).status_code == 200
        # refresh keeps the session in the deactivated tenant
        r = await _call(c, "POST", REFRESH, refresh=refresh, headers=XRW)
        assert r.status_code == 200, r.text
        assert _claims(_cookies(r)[COOKIE].value)["active_tenant_id"] == t.id
        # and can reactivate it
        r = await _call(c, "PUT", f"/api/v1/tenants/{t.id}", headers=_bearer(access2), json={"is_active": True})
        assert r.status_code == 200, r.text
        del other
    _go(body)
