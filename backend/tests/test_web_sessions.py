"""Web sessions: every member sign-in is a server-side session (``sid`` in a 1 h access token) with a
rotating refresh token; revoked sessions are refused on every request."""
import time
from datetime import timedelta
from http.cookies import SimpleCookie

import jwt
from sqlalchemy import delete, select

from app.api.v1 import mfa as mfa_mod
from app.api.v1 import sso as sso_mod
from app.auth import sessions
from app.core import login_throttle, security, totp
from app.core.config import settings
from app.core.security import get_password_hash
from app.core.session_cookie import COOKIE, REFRESH_COOKIE
from app.utils.crypto import decrypt, encrypt
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.auth_session import AuthSession
from app.models.membership import TenantMembership
from app.models.user import User
from app.utils.crypto import encrypt
from tests.audit_fixtures import _FakeThrottle
from tests.guest_fixtures import FakeAsyncRedis, client, make_tenant, run
from tests.session_fixtures import make_web_token
from tests.test_guest_sso import FakeAuth

PW = "Sessions-Passw0rd!x"
NEW_PW = "Sessions-N3w-Passw0rd!y"
LOGIN = "/api/v1/auth/login/access-token"
REFRESH = "/api/v1/auth/refresh"
XRW = {"X-Requested-With": "XMLHttpRequest"}


def _overrides():
    redis = FakeAsyncRedis()
    app.dependency_overrides[login_throttle.get_login_throttle] = lambda: _FakeThrottle()
    app.dependency_overrides[login_throttle.get_pwd_throttle] = lambda: _FakeThrottle()
    app.dependency_overrides[mfa_mod.get_mfa_throttle] = lambda: _FakeThrottle()
    app.dependency_overrides[mfa_mod.get_redis] = lambda: redis


async def _tenant(db, ids, method="password", sso=False):
    t = await make_tenant(db, ids, sso=sso)
    t.member_login_method = method
    await db.commit()
    return t


async def _user(db, ids, name, memberships, *, mfa_secret=None) -> User:
    u = User(email=f"g-{ids.h}-{name}@example.com", hashed_password=get_password_hash(PW), is_active=True)
    if mfa_secret:
        from datetime import datetime, timezone
        u.mfa_secret_enc = encrypt(mfa_secret)
        u.mfa_enabled_at = datetime.now(timezone.utc)
    db.add(u)
    await db.flush()
    ids.users.append(int(u.id))
    db.add_all([TenantMembership(user_id=u.id, tenant_id=t.id, role=role) for t, role in memberships])
    await db.commit()
    return u


def _go(body, *args):
    async def wrapped(ids):
        _overrides()
        try:
            async with client() as c:
                await body(c, ids, *args)
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(ids.users or [-1])))
                await db.commit()
    run(wrapped)


def _cookies(r) -> dict:
    out = {}
    for raw in r.headers.get_list("set-cookie"):
        c = SimpleCookie()
        c.load(raw)
        out.update({k: c[k] for k in c})
    return out


def _claims(tok):
    return jwt.decode(tok, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def _bearer(t):
    return {"Authorization": f"Bearer {t}"}


async def _call(c, method, path, *, access=None, refresh=None, headers=None, **kw):
    """Send exactly the credentials under test (the client's cookie jar is cleared first)."""
    c.cookies.clear()
    jar = "; ".join(f"{k}={v}" for k, v in ((COOKIE, access), (REFRESH_COOKIE, refresh)) if v)
    h = {**(headers or {}), **({"Cookie": jar} if jar else {})}
    return await c.request(method, path, headers=h, **kw)


async def _spa_login(c, email):
    r = await _call(c, "POST", LOGIN, headers=XRW, data={"username": email, "password": PW})
    assert r.status_code == 200, r.text
    ck = _cookies(r)
    return ck[COOKIE].value, ck[REFRESH_COOKIE].value


async def _api_login(c, email):
    r = await _call(c, "POST", LOGIN, data={"username": email, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["access_token"], r.json()["refresh_token"]


async def _row(sid) -> AuthSession:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(AuthSession).where(AuthSession.id == sid))).scalar_one()


async def _me(c, access):
    return await _call(c, "GET", "/api/v1/users/me", headers=_bearer(access))


def test_login_sets_refresh_cookie():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        r = await _call(c, "POST", LOGIN, headers=XRW, data={"username": u.email, "password": PW})
        assert r.status_code == 200, r.text
        assert "access_token" not in r.json() and "refresh_token" not in r.json()
        ck = _cookies(r)
        secure = settings.PUBLIC_BASE_URL.startswith("https")
        ref = ck[REFRESH_COOKIE]
        assert ref["httponly"] and ref["samesite"].lower() == "strict"
        assert ref["path"] == "/api/v1/auth" and ref["max-age"] == str(30 * 86400)
        assert bool(ref["secure"]) == secure
        acc = ck[COOKIE]
        assert acc["httponly"] and acc["samesite"].lower() == "strict" and acc["path"] == "/api"
        assert acc["max-age"] == "3600" and bool(acc["secure"]) == secure
        sid = _claims(acc.value)["sid"]
        assert decrypt(ref.value).startswith(sid + ".")  # the cookie is sealed; the token inside names the session
        row = await _row(sid)
        assert row.kind == "web" and row.amr == "pwd" and row.tenant_id == t.id and row.user_id == u.id
    _go(body)


def test_refresh_rotates_and_new_access_works():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        access, refresh = await _spa_login(c, u.email)
        r = await _call(c, "POST", REFRESH, refresh=refresh, headers=XRW)
        assert r.status_code == 200, r.text
        assert "access_token" not in r.json() and "refresh_token" not in r.json()
        ck = _cookies(r)
        new_access, new_refresh = ck[COOKIE].value, ck[REFRESH_COOKIE].value
        assert new_refresh != refresh and _claims(new_access)["sid"] == _claims(access)["sid"]
        me = await _call(c, "GET", "/api/v1/users/me", access=new_access)
        assert me.status_code == 200 and me.json()["active_tenant_id"] == t.id
        # The rotated-away secret, presented after the grace window, is reuse: the session dies.
        async with AsyncSessionLocal() as db:
            row = await db.get(AuthSession, _claims(access)["sid"])
            row.prev_refresh_hash = None
            await db.commit()
        bad = await _call(c, "POST", REFRESH, refresh=refresh, headers=XRW)
        assert bad.status_code == 401
        cleared = _cookies(bad)
        assert cleared[COOKIE]["max-age"] == "0" and cleared[REFRESH_COOKIE]["max-age"] == "0"
        assert cleared[REFRESH_COOKIE]["path"] == "/api/v1/auth"
        assert (await _call(c, "GET", "/api/v1/users/me", access=new_access)).status_code == 401
    _go(body)


def test_refresh_keeps_switched_tenant():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            ta, tb = await _tenant(db, ids), await _tenant(db, ids)
            u = await _user(db, ids, "a", [(ta, "analyst"), (tb, "analyst")])
        access, refresh = await _spa_login(c, u.email)
        sw = await _call(c, "POST", "/api/v1/auth/switch-tenant", access=access, headers=XRW,
                         json={"tenant_id": tb.id})
        assert sw.status_code == 200, sw.text
        ck = _cookies(sw)
        assert REFRESH_COOKIE not in ck  # same session, same refresh token
        assert _claims(ck[COOKIE].value)["sid"] == _claims(access)["sid"]
        assert (await _row(_claims(access)["sid"])).tenant_id == tb.id
        r = await _call(c, "POST", REFRESH, refresh=refresh, headers=XRW)
        assert r.status_code == 200, r.text
        assert _claims(_cookies(r)[COOKIE].value)["active_tenant_id"] == tb.id
    _go(body)


def test_refresh_falls_back_when_tenant_not_enterable():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            ta, tb = await _tenant(db, ids), await _tenant(db, ids)
            u = await _user(db, ids, "a", [(ta, "analyst"), (tb, "analyst")])
        access, refresh = await _api_login(c, u.email)
        sw = await _call(c, "POST", "/api/v1/auth/switch-tenant", headers=_bearer(access), json={"tenant_id": tb.id})
        assert sw.status_code == 200, sw.text
        async with AsyncSessionLocal() as db:  # membership in B is gone
            await db.execute(delete(TenantMembership).where(TenantMembership.user_id == u.id,
                                                            TenantMembership.tenant_id == tb.id))
            await db.commit()
        r = await _call(c, "POST", REFRESH, json={"refresh_token": refresh})
        assert r.status_code == 200, r.text
        assert _claims(r.json()["access_token"])["active_tenant_id"] == ta.id
        assert (await _row(_claims(access)["sid"])).tenant_id == ta.id
        refresh = r.json()["refresh_token"]
        async with AsyncSessionLocal() as db:  # A switches to SSO-only: no tenant left for a pwd session
            (await db.get(type(ta), ta.id)).member_login_method = "saml"
            await db.commit()
        r = await _call(c, "POST", REFRESH, json={"refresh_token": refresh})
        assert r.status_code == 401, r.text
    _go(body)


def test_refresh_requires_csrf_header_for_cookie():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        _, refresh = await _spa_login(c, u.email)
        r = await _call(c, "POST", REFRESH, refresh=refresh)
        assert r.status_code == 403
        # the refusal did not spend the refresh token
        assert (await _call(c, "POST", REFRESH, refresh=refresh, headers=XRW)).status_code == 200
    _go(body)


def test_refresh_without_credentials_401():
    async def body(c, ids):
        assert (await _call(c, "POST", REFRESH, headers=XRW)).status_code == 401
        assert (await _call(c, "POST", REFRESH, json={"refresh_token": "nope"})).status_code == 401
    _go(body)


def test_refresh_race_sets_only_access_cookie():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        _, refresh = await _api_login(c, u.email)
        first = await _call(c, "POST", REFRESH, json={"refresh_token": refresh})
        assert first.status_code == 200
        # the loser of a concurrent refresh (same secret, inside the grace window)
        second = await _call(c, "POST", REFRESH, json={"refresh_token": refresh})
        assert second.status_code == 200, second.text
        assert second.json()["access_token"] and "refresh_token" not in second.json()
        assert REFRESH_COOKIE not in _cookies(second) and COOKIE in _cookies(second)
        assert (await _me(c, second.json()["access_token"])).status_code == 200
        # the winner's secret is still the current one
        assert (await _call(c, "POST", REFRESH, json={"refresh_token": first.json()["refresh_token"]})
                ).status_code == 200
    _go(body)


def test_logout_revokes_session():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        access, refresh = await _spa_login(c, u.email)
        r = await _call(c, "POST", "/api/v1/auth/logout", access=access, headers=XRW)
        assert r.status_code == 204
        ck = _cookies(r)
        assert ck[COOKIE]["max-age"] == "0" and ck[REFRESH_COOKIE]["max-age"] == "0"
        assert (await _row(_claims(access)["sid"])).revoked_at is not None
        assert (await _call(c, "GET", "/api/v1/users/me", access=access)).status_code == 401
        assert (await _call(c, "POST", REFRESH, refresh=refresh, headers=XRW)).status_code == 401
    _go(body)


def test_revoked_session_access_401():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
            tok = await make_web_token(db, u, t.id)
        assert (await _me(c, tok)).status_code == 200
        async with AsyncSessionLocal() as db:
            assert await sessions.revoke(db, _claims(tok)["sid"], user_id=u.id)
            await db.commit()
        assert (await _me(c, tok)).status_code == 401
    _go(body)


def test_token_without_sid_401():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
            other = await _user(db, ids, "b", [(t, "analyst")])
            other_tok = await make_web_token(db, other, t.id)
        claims = {"sub": u.email, "active_tenant_id": t.id, "tv": 0, "amr": "pwd", "sso_tid": None}
        assert (await _me(c, security.create_access_token(claims))).status_code == 401
        assert (await _me(c, security.create_access_token({**claims, "sid": "0" * 32}))).status_code == 401
        # a live session of someone else does not carry this user
        stolen = security.create_access_token({**claims, "sid": _claims(other_tok)["sid"]})
        assert (await _me(c, stolen)).status_code == 401
    _go(body)


def test_api_client_gets_refresh_in_body():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        access, refresh = await _api_login(c, u.email)
        assert refresh.startswith(_claims(access)["sid"] + ".")
        r = await _call(c, "POST", REFRESH, json={"refresh_token": refresh})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["access_token"] and j["refresh_token"] and j["refresh_token"] != refresh
        assert (await _me(c, j["access_token"])).status_code == 200
    _go(body)


def test_refresh_cookie_wins_over_body():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        _, cookie_refresh = await _spa_login(c, u.email)
        _, body_refresh = await _api_login(c, u.email)
        r = await _call(c, "POST", REFRESH, refresh=cookie_refresh, headers=XRW,
                        json={"refresh_token": body_refresh})
        assert r.status_code == 200, r.text
        assert "access_token" not in r.json() and "refresh_token" not in r.json()
        assert _claims(_cookies(r)[COOKIE].value)["sid"] == decrypt(cookie_refresh).split(".")[0]
        # the body's token was ignored: still current (not rotated, not revoked)
        b = await _call(c, "POST", REFRESH, json={"refresh_token": body_refresh})
        assert b.status_code == 200 and b.json()["refresh_token"]
    _go(body)


def test_saml_login_creates_session(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "saml", sso=True)
            u = await _user(db, ids, "a", [(t, "analyst")])
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(u.email))
        r = await _call(c, "POST", f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x"})
        assert r.status_code == 303, r.text
        ck = _cookies(r)
        access, refresh = ck[COOKIE].value, ck[REFRESH_COOKIE].value
        row = await _row(_claims(access)["sid"])
        assert (row.kind, row.amr, row.sso_tenant_id, row.tenant_id) == ("web", "saml", t.id, t.id)
        r2 = await _call(c, "POST", REFRESH, refresh=refresh, headers=XRW)
        assert r2.status_code == 200, r2.text
        new = _claims(_cookies(r2)[COOKIE].value)
        assert new["amr"] == "saml" and new["sso_tid"] == t.id and new["active_tenant_id"] == t.id
    _go(body)


async def _two_sessions(c, email):
    """Session A (SPA cookies) and session B (API client)."""
    a_access, a_refresh = await _spa_login(c, email)
    b_access, b_refresh = await _api_login(c, email)
    return a_access, a_refresh, b_access, b_refresh


async def _assert_only_current_survives(c, r, a_access, b_refresh):
    assert r.status_code == 200, r.text
    assert "access_token" not in r.json() and "refresh_token" not in r.json()
    ck = _cookies(r)
    new_access, new_refresh = ck[COOKIE].value, ck[REFRESH_COOKIE].value
    sid = _claims(a_access)["sid"]
    assert _claims(new_access)["sid"] == sid
    assert (await _call(c, "GET", "/api/v1/users/me", access=new_access)).status_code == 200
    ref = await _call(c, "POST", REFRESH, refresh=new_refresh, headers=XRW)
    assert ref.status_code == 200, ref.text
    assert (await _call(c, "POST", REFRESH, json={"refresh_token": b_refresh})).status_code == 401
    assert (await _row(b_refresh.split(".")[0])).revoked_at is not None
    assert (await _row(sid)).revoked_at is None


def test_refresh_works_after_password_change():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        a_access, _, _, b_refresh = await _two_sessions(c, u.email)
        r = await _call(c, "POST", "/api/v1/users/me/password", access=a_access, headers=XRW,
                        json={"current_password": PW, "new_password": NEW_PW})
        await _assert_only_current_survives(c, r, a_access, b_refresh)
    _go(body)


def test_refresh_works_after_mfa_enable():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        a_access, _, _, b_refresh = await _two_sessions(c, u.email)
        s = await _call(c, "POST", "/api/v1/users/me/mfa/setup", access=a_access, headers=XRW)
        assert s.status_code == 200, s.text
        r = await _call(c, "POST", "/api/v1/users/me/mfa/enable", access=a_access, headers=XRW,
                        json={"code": totp.totp_at(s.json()["secret"], time.time()), "current_password": PW})
        await _assert_only_current_survives(c, r, a_access, b_refresh)
    _go(body)


def test_refresh_works_after_mfa_disable():
    async def body(c, ids):
        secret = totp.new_secret()
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")], mfa_secret=secret)
            # MFA users sign in in two steps; sessions made directly keep this test about the disable.
            a_access = await make_web_token(db, u, t.id)
            b_row, b_refresh = await sessions.create_session(db, u, kind="web", amr="pwd", tenant_id=t.id)
            await db.commit()
        r = await _call(c, "POST", "/api/v1/users/me/mfa/disable", access=a_access, headers=XRW,
                        json={"current_password": PW, "code": totp.totp_at(secret, time.time())})
        await _assert_only_current_survives(c, r, a_access, b_refresh)
    _go(body)


LOGOUT = "/api/v1/auth/logout"


def _all_cleared(r):
    ck = _cookies(r)
    assert ck[COOKIE]["max-age"] == "0" and ck[REFRESH_COOKIE]["max-age"] == "0"
    assert ck[REFRESH_COOKIE]["path"] == "/api/v1/auth"


def test_logout_revokes_policy_refused_session():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        access, refresh = await _spa_login(c, u.email)
        async with AsyncSessionLocal() as db:  # the tenant moves to SSO: this pwd session is now refused
            (await db.get(type(t), t.id)).member_login_method = "oidc"
            await db.commit()
        assert (await _call(c, "GET", "/api/v1/users/me", access=access)).status_code == 403
        r = await _call(c, "POST", LOGOUT, access=access, refresh=refresh, headers=XRW)
        assert r.status_code == 204, r.text
        _all_cleared(r)
        assert (await _row(_claims(access)["sid"])).revoked_at is not None
    _go(body)


def test_logout_with_only_refresh_cookie_revokes():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        access, refresh = await _spa_login(c, u.email)
        r = await _call(c, "POST", LOGOUT, refresh=refresh, headers=XRW)  # access cookie expired/absent
        assert r.status_code == 204, r.text
        _all_cleared(r)
        assert (await _row(_claims(access)["sid"])).revoked_at is not None
    _go(body)


def test_logout_with_expired_access_token_revokes():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        access, _ = await _api_login(c, u.email)
        expired = security.create_access_token({k: v for k, v in _claims(access).items() if k != "exp"},
                                               expires_delta=timedelta(seconds=-60))
        r = await _call(c, "POST", LOGOUT, headers=_bearer(expired))
        assert r.status_code == 204, r.text
        assert (await _row(_claims(access)["sid"])).revoked_at is not None
    _go(body)


def test_logout_forged_refresh_does_not_revoke():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
            other = await _user(db, ids, "b", [(t, "analyst")])
        access, _ = await _spa_login(c, u.email)
        sid = _claims(access)["sid"]
        r = await _call(c, "POST", LOGOUT, refresh=encrypt(f"{sid}.forged-secret"), headers=XRW)
        assert r.status_code == 204, r.text
        _all_cleared(r)
        assert (await _row(sid)).revoked_at is None
        # a validly signed token of another user naming this sid does not revoke it either
        o_access, _ = await _api_login(c, other.email)
        forged = security.create_access_token({**{k: v for k, v in _claims(o_access).items() if k != "exp"},
                                               "sid": sid})
        assert (await _call(c, "POST", LOGOUT, headers=_bearer(forged))).status_code == 204
        assert (await _row(sid)).revoked_at is None
    _go(body)


def test_logout_cookie_without_csrf_header_403():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        access, refresh = await _spa_login(c, u.email)
        assert (await _call(c, "POST", LOGOUT, access=access, refresh=refresh)).status_code == 403
        assert (await _call(c, "POST", LOGOUT, refresh=refresh)).status_code == 403
        assert (await _row(_claims(access)["sid"])).revoked_at is None
    _go(body)


def test_logout_without_credentials_sets_no_cookies():
    """A credential-less POST (e.g. a cross-site form) must not be able to clear the cookies."""
    async def body(c, ids):
        r = await _call(c, "POST", LOGOUT)
        assert r.status_code == 204
        assert "set-cookie" not in r.headers
    _go(body)


def test_unreadable_refresh_cookie_is_refused_and_cleared():
    """A refresh cookie that can't be unsealed (tampered, or set before sealing) still wins over a body token:
    the refresh is refused with both cookies cleared, so the browser signs in again."""
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids)
            u = await _user(db, ids, "a", [(t, "analyst")])
        _, body_refresh = await _api_login(c, u.email)
        r = await _call(c, "POST", REFRESH, refresh="pre-sealing-raw-token", headers=XRW,
                        json={"refresh_token": body_refresh})
        assert r.status_code == 401
        ck = _cookies(r)
        assert ck[COOKIE]["max-age"] == "0" and ck[REFRESH_COOKIE]["max-age"] == "0"
        # logout with only an unreadable refresh cookie still clears cookies (a credential was presented)
        out = await _call(c, "POST", LOGOUT, refresh="pre-sealing-raw-token", headers=XRW)
        assert out.status_code == 204 and _cookies(out)[REFRESH_COOKIE]["max-age"] == "0"
    _go(body)
