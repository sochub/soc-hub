"""The guest-portal session is an HttpOnly cookie scoped to the guest API; the SPA never sees the token.
API clients (no X-Requested-With) keep receiving it in the body. Guest and member cookies never cross over."""
from http.cookies import SimpleCookie

from sqlalchemy import delete

from app.api.v1 import guest_auth, sso as sso_mod
from app.core import login_throttle
from app.core.config import settings
from app.core.security import get_password_hash
from app.core.session_cookie import COOKIE, GUEST_COOKIE, GUEST_PATH
from app.db.session import AsyncSessionLocal
from app.guests import tokens
from app.main import app
from app.models.membership import TenantMembership
from app.models.user import User
from tests.audit_fixtures import _FakeThrottle
from tests.guest_fixtures import PW, client, make_account, make_case, make_grant, make_tenant, run
from tests.test_guest_sso import FakeAuth

XRW = {"X-Requested-With": "XMLHttpRequest"}


def _cookies(r) -> SimpleCookie:
    c = SimpleCookie()
    for raw in r.headers.get_list("set-cookie"):
        c.load(raw)
    return c


def _guest_cookie(r):
    m = _cookies(r)[GUEST_COOKIE]
    assert m["httponly"] and m["samesite"].lower() == "strict" and m["path"] == GUEST_PATH
    assert bool(m["secure"]) == settings.PUBLIC_BASE_URL.startswith("https")
    return m


def test_guest_cookie_sign_in_use_isolation_and_logout():
    async def body(ids):
        app.dependency_overrides[guest_auth.get_guest_throttle] = lambda: _FakeThrottle()
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: _FakeThrottle()
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await make_case(db, ids, t)
            e_link = ids.email("cklink")
            g = await make_grant(db, case, e_link, permission="contributor")
            e_pw = ids.email("ckpw")
            await make_grant(db, case, e_pw)
            await make_account(db, e_pw)
            u = User(email=f"g-{ids.h}-member@example.com", hashed_password=get_password_hash(PW), is_active=True)
            db.add(u)
            await db.flush()
            ids.users.append(int(u.id))
            db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
            await db.commit()
        try:
            async with client() as c:
                async def call(method, path, cookies=None, headers=None, **kw):
                    c.cookies.clear()
                    jar = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
                    h = {**(headers or {}), **({"Cookie": jar} if jar else {})}
                    return await c.request(method, path, headers=h, **kw)

                # SPA magic-link sign-in: cookie only, no token in the body
                r = await call("POST", "/api/v1/guest/auth/link", headers=XRW, json={"token": tokens.issue_link(g.id)})
                assert r.status_code == 200, r.text
                assert "access_token" not in r.json()
                m = _guest_cookie(r)
                assert m["max-age"] == str(int(tokens.SESSION_TTL.total_seconds()))
                assert COOKIE not in _cookies(r)  # never the member session cookie
                gsess = m.value
                assert tokens.decode(gsess, tokens.SESSION)["email"] == e_link

                # SPA password sign-in: same
                r = await call("POST", "/api/v1/guest/auth/login", headers=XRW, json={"email": e_pw, "password": PW})
                assert r.status_code == 200 and "access_token" not in r.json()
                assert tokens.decode(_guest_cookie(r).value, tokens.SESSION)["email"] == e_pw

                # API client (no X-Requested-With): token stays in the body
                r = await call("POST", "/api/v1/guest/auth/login", json={"email": e_pw, "password": PW})
                assert r.status_code == 200 and r.json()["access_token"]

                # the cookie authenticates guest routes
                gc = {GUEST_COOKIE: gsess}
                assert (await call("GET", "/api/v1/guest/cases", cookies=gc)).status_code == 200
                assert (await call("GET", "/api/v1/guest/cases")).status_code == 401
                # unsafe method on the cookie needs the CSRF header
                path = f"/api/v1/guest/cases/{case.id}/comments"
                assert (await call("POST", path, cookies=gc, json={"content": "hi"})).status_code == 403
                assert (await call("POST", path, cookies=gc, headers=XRW, json={"content": "hi"})).status_code == 201

                # guest cookie never opens a member route (by its own name, or smuggled into the member one)
                assert (await call("GET", "/api/v1/users/me", cookies=gc)).status_code == 401
                assert (await call("GET", "/api/v1/users/me", cookies={COOKIE: gsess})).status_code == 401

                # a member session cookie never opens a guest route
                r = await call("POST", "/api/v1/auth/login/access-token", headers=XRW,
                               data={"username": u.email, "password": PW})
                assert r.status_code == 200, r.text
                msess = _cookies(r)[COOKIE].value
                assert (await call("GET", "/api/v1/users/me", cookies={COOKIE: msess})).status_code == 200
                assert (await call("GET", "/api/v1/guest/cases", cookies={COOKIE: msess})).status_code == 401
                assert (await call("GET", "/api/v1/guest/cases", cookies={GUEST_COOKIE: msess})).status_code == 401

                # logout: CSRF header required with a cookie, then the cookie is cleared
                assert (await call("POST", "/api/v1/guest/auth/logout", cookies=gc)).status_code == 403
                r = await call("POST", "/api/v1/guest/auth/logout", cookies=gc, headers=XRW)
                assert r.status_code == 204 and _guest_cookie(r)["max-age"] == "0"
                # credential-less logout (cross-site form) does nothing
                r = await call("POST", "/api/v1/guest/auth/logout")
                assert r.status_code == 204 and GUEST_COOKIE not in _cookies(r)
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id == u.id))
                await db.commit()
    run(body)


def test_guest_sso_sets_cookie_and_no_token_in_redirect(monkeypatch):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids, require_sso=True, sso=True, idp_initiated=True)
            case = await make_case(db, ids, t)
            e = ids.email("cksso")
            await make_grant(db, case, e)
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(e))
        async with client() as c:
            r = await c.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x", "RelayState": "guest"})
            loc = r.headers["location"]
            assert r.status_code == 303 and loc.endswith("/guest/sso") and "token" not in loc, loc
            tok = _guest_cookie(r).value
            p = tokens.decode(tok, tokens.SESSION)
            assert p["amr"] == "sso" and p["sso_tenant_id"] == t.id and p["email"] == e
            c.cookies.clear()
            assert (await c.get(f"/api/v1/guest/cases/{case.id}",
                                headers={"Cookie": f"{GUEST_COOKIE}={tok}"})).status_code == 200
    run(body)
