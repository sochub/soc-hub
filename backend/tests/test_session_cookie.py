"""The browser session is an HttpOnly cookie the SPA never sees as a token; API clients keep bearer tokens."""
from http.cookies import SimpleCookie

from sqlalchemy import delete

from app.core import login_throttle
from app.core.security import get_password_hash
from app.core.session_cookie import COOKIE
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.membership import TenantMembership
from app.models.user import User
from tests.audit_fixtures import _FakeThrottle
from tests.guest_fixtures import client, make_tenant, run

PW = "Cookie-Passw0rd!x"
XRW = {"X-Requested-With": "XMLHttpRequest"}


def _cookie(r):
    c = SimpleCookie()
    c.load(r.headers["set-cookie"])
    return c[COOKIE]


def test_browser_session_cookie():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            ta, tb = await make_tenant(db, ids), await make_tenant(db, ids)
            u = User(email=f"g-{ids.h}-cookie@example.com", hashed_password=get_password_hash(PW), is_active=True)
            db.add(u)
            await db.flush()
            ids.users.append(int(u.id))
            db.add_all([TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst") for t in (ta, tb)])
            await db.commit()
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: _FakeThrottle()
        form = {"username": u.email, "password": PW}
        try:
            async with client() as c:
                async def call(method, path, cookie=None, headers=None, **kw):
                    c.cookies.clear()  # send exactly the credentials under test
                    h = {**(headers or {}), **({"Cookie": f"{COOKIE}={cookie}"} if cookie else {})}
                    return await c.request(method, path, headers=h, **kw)

                # SPA login: cookie only, never a token in the body
                r = await call("POST", "/api/v1/auth/login/access-token", headers=XRW, data=form)
                assert r.status_code == 200 and "access_token" not in r.json()
                m = _cookie(r)
                assert m["httponly"] and m["samesite"].lower() == "strict" and m["path"] == "/api"
                sess = m.value

                # API client login: bearer token in the body, as before
                r = await call("POST", "/api/v1/auth/login/access-token", data=form)
                bearer = r.json()["access_token"]

                assert (await call("GET", "/api/v1/users/me", cookie=sess)).status_code == 200
                assert (await call("GET", "/api/v1/users/me")).status_code == 401

                # cookie + unsafe method needs the CSRF header
                switch = {"tenant_id": tb.id}
                assert (await call("POST", "/api/v1/auth/switch-tenant", cookie=sess, json=switch)).status_code == 403
                r = await call("POST", "/api/v1/auth/switch-tenant", cookie=sess, headers=XRW, json=switch)
                assert r.status_code == 200 and "access_token" not in r.json() and _cookie(r).value

                # a bearer caller still gets its new token in the body
                r = await call("POST", "/api/v1/auth/switch-tenant",
                               headers={"Authorization": f"Bearer {bearer}"}, json=switch)
                assert r.status_code == 200 and r.json()["access_token"]

                r = await call("POST", "/api/v1/auth/logout", cookie=sess, headers=XRW)
                assert r.status_code == 204 and _cookie(r)["max-age"] == "0"
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id == u.id))
                await db.commit()
    run(body)
