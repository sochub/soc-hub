"""OIDC sign-in routes (members + guests), the SSO dispatcher and the SSO config API, against a fake IdP."""
import base64
import hashlib
import json
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, unquote, urlparse

import jwt
from sqlalchemy import delete, func, select

from app.api import deps
from app.api.v1 import sso as sso_mod
from app.core.config import settings
from app.core.session_cookie import COOKIE
from app.db.session import AsyncSessionLocal
from app.guests import tokens
from app.main import app
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user import User
from app.core.security import get_password_hash
from app.services import oidc_service
from app.utils.crypto import decrypt, encrypt
from tests.guest_fixtures import client, make_case, make_grant, make_tenant, make_user, run
from tests.oidc_fixtures import CLIENT_ID, ISSUER, discovery_doc, jwks_for, make_key, new_kid, sign_token
from tests.test_guest_sso import FakeAuth

SECRET = "oidc-client-s3cret"


class FakeIdP:
    """Discovery, JWKS and a token endpoint that checks PKCE and signs an ID token with the request's nonce."""

    def __init__(self, monkeypatch, *, auth_endpoint=None):
        oidc_service._clear_caches()
        self.key, self.kid = make_key(), new_kid()
        self.email = "nobody@corp.example"
        self.claims = {}
        self.pending = {}
        self.binds = {}  # state -> the browser-binding cookie set by the login route
        doc = discovery_doc()
        if auth_endpoint:
            doc["authorization_endpoint"] = auth_endpoint

        async def get_json(url, code="discovery"):
            if url == f"{ISSUER}/.well-known/openid-configuration":
                return doc
            if url == f"{ISSUER}/jwks":
                return jwks_for((self.key, self.kid))
            raise AssertionError(f"unexpected GET {url}")

        async def post_form(url, data, code="token"):
            assert url == f"{ISSUER}/token"
            q = self.pending.pop(data["code"])
            digest = hashlib.sha256(data["code_verifier"].encode()).digest()
            assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == q["code_challenge"]
            assert data["client_secret"] == SECRET and data["redirect_uri"] == q["redirect_uri"]
            return {"id_token": sign_token(self.key, self.kid, nonce=q["nonce"], email=self.email, **self.claims)}

        monkeypatch.setattr(oidc_service, "_get_json", get_json)
        monkeypatch.setattr(oidc_service, "_post_form", post_form)
        FakeIdP.current = self

    def authorize(self, location: str) -> tuple[str, str]:
        q = {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}
        code = secrets.token_hex(8)
        self.pending[code] = q
        return code, q["state"]


async def _oidc_tenant(db, ids, *, member="oidc", guest="password", auto_provision=False) -> Tenant:
    t = await make_tenant(db, ids)
    t.member_login_method, t.guest_login_method = member, guest
    db.add(TenantSSOConfig(tenant_id=t.id, oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID,
                           oidc_client_secret_enc=encrypt(SECRET), auto_provision=auto_provision,
                           default_role="viewer"))
    await db.commit()
    return t


async def _member(db, ids, name, tenants, role="analyst") -> User:
    u = User(email=f"g-{ids.h}-{name}@corp.example", hashed_password=get_password_hash(secrets.token_hex(8)),
             is_active=True)
    db.add(u)
    await db.flush()
    ids.users.append(int(u.id))
    db.add_all([TenantMembership(user_id=u.id, tenant_id=t.id, role=role) for t in tenants])
    await db.commit()
    return u


def _go(body):
    async def wrapped(ids):
        try:
            async with client() as c:
                await body(c, ids)
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(ids.users or [-1])))
                await db.commit()
    run(wrapped)


async def _start(c, idp, slug, guest=False):
    r = await c.get(f"/api/v1/auth/oidc/{slug}/login" + ("?guest=1" if guest else ""))
    assert r.status_code == 303, r.text
    loc = r.headers["location"]
    assert loc.startswith(f"{ISSUER}/authorize?"), loc
    cookie = SimpleCookie()
    cookie.load(r.headers["set-cookie"])
    m = cookie[sso_mod.OIDC_COOKIE]
    assert m["httponly"] and m["samesite"].lower() == "lax" and m["path"] == "/api/v1/auth/oidc"
    code, state = idp.authorize(loc)
    idp.binds[state] = m.value
    return code, state


async def _callback(c, slug, code, state, bind=None):
    """Call back as the browser holding `bind` (default: the one that started this state)."""
    c.cookies.clear()
    bind = bind if bind is not None else FakeIdP.current.binds.get(state, "")
    return await c.get(f"/api/v1/auth/oidc/{slug}/callback", params={"code": code, "state": state},
                       headers={"Cookie": f"{sso_mod.OIDC_COOKIE}={bind}"} if bind else {})


async def _flow(c, idp, slug, guest=False):
    code, state = await _start(c, idp, slug, guest)
    return await _callback(c, slug, code, state)


def _err(r) -> str:
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "#sso_error=" in loc and f"{COOKIE}=" not in r.headers.get("set-cookie", "") and "token=" not in loc, loc
    return unquote(loc.split("#sso_error=", 1)[1])


def _cookie(r) -> str:
    c = SimpleCookie()
    for raw in r.headers.get_list("set-cookie"):  # one header per cookie (session, refresh, state)
        c.load(raw)
    return c[COOKIE].value


async def _reasons(tid):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == tid,
                                                        AuditLog.action == "sso.failed"))).scalars().all()
    return [r.changes["reason"] for r in rows]


def test_oidc_member_login(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [t])
        idp = FakeIdP(monkeypatch)
        idp.email = u.email.upper()  # matched case-insensitively
        r = await c.get(f"/api/v1/auth/sso/{t.slug}/login")
        assert r.status_code == 303 and f"/auth/oidc/{t.slug}/login" in r.headers["location"]
        r = await c.get(f"/api/v1/auth/oidc/{t.slug}/login")
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["code_challenge_method"] == ["S256"] and q["client_id"] == [CLIENT_ID]
        assert q["redirect_uri"] == [sso_mod.oidc_redirect_uri(t.slug)]
        assert q["redirect_uri"][0] == f"{settings.PUBLIC_BASE_URL}/api/v1/auth/oidc/{t.slug}/callback"
        assert q["scope"] == ["openid email profile"] and q["nonce"][0] and q["state"][0]
        stored = json.loads(sso_mod._oidc_redis().vals[f"oidc:state:{q['state'][0]}"])
        assert stored["tenant_id"] == t.id and stored["guest"] is False and stored["nonce"] == q["nonce"][0]
        assert sso_mod._oidc_redis().keys[f"oidc:state:{q['state'][0]}"] == 600
        code, state = idp.authorize(r.headers["location"])
        idp.binds[state] = c.cookies.get(sso_mod.OIDC_COOKIE)
        r = await _callback(c, t.slug, code, state)
        assert r.status_code == 303 and r.headers["location"].endswith("/login#sso=ok"), r.headers["location"]
        sess = _cookie(r)
        claims = jwt.decode(sess, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert claims["amr"] == "oidc" and claims["sso_tid"] == t.id
        c.cookies.clear()
        me = await c.get("/api/v1/users/me", headers={"Cookie": f"{COOKIE}={sess}"})
        assert me.status_code == 200 and me.json()["active_tenant_id"] == t.id
    _go(body)


def test_oidc_state_single_use(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [t])
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        code, state = await _start(c, idp, t.slug)
        idp.pending["replayed"] = dict(idp.pending[code])
        assert (await _callback(c, t.slug, code, state)).headers["location"].endswith("#sso=ok")
        _err(await _callback(c, t.slug, "replayed", state))
        assert await _reasons(t.id) == ["state"]
    _go(body)


def test_oidc_missing_state(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
        FakeIdP(monkeypatch)
        _err(await _callback(c, t.slug, "x", "no-such-state"))
        _err(await c.get(f"/api/v1/auth/oidc/{t.slug}/callback", params={"code": "x"}))
        assert await _reasons(t.id) == ["state", "state"]
    _go(body)


def test_oidc_state_bound_to_browser(monkeypatch):
    """Login CSRF: a callback URL carried to another browser (no / wrong binding cookie) is refused,
    and the state is spent by the attempt."""
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [t])
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        code, state = await _start(c, idp, t.slug)
        _err(await _callback(c, t.slug, code, state, bind=""))
        code, state = await _start(c, idp, t.slug)
        _err(await _callback(c, t.slug, code, state, bind="someone-elses-cookie"))
        _err(await _callback(c, t.slug, code, state))  # state already consumed
        assert await _reasons(t.id) == ["state", "state", "state"]
        r = await _callback(c, t.slug, *(await _start(c, idp, t.slug)))
        assert r.headers["location"].endswith("#sso=ok")
        assert f"{sso_mod.OIDC_COOKIE}=\"\"" in r.headers.get_list("set-cookie")[-1] or \
            "max-age=0" in r.headers.get_list("set-cookie")[-1].lower()
    _go(body)


def test_oidc_state_bound_to_tenant(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            ta = await _oidc_tenant(db, ids)
            tb = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [ta, tb])
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        code, state = await _start(c, idp, ta.slug)
        _err(await _callback(c, tb.slug, code, state))
        assert await _reasons(tb.id) == ["state"]
    _go(body)


def test_oidc_idp_error(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
        idp = FakeIdP(monkeypatch)
        _, state = await _start(c, idp, t.slug)
        r = await c.get(f"/api/v1/auth/oidc/{t.slug}/callback",
                        params={"state": state, "error": "access_denied", "error_description": "<b>evil</b>"})
        assert "evil" not in _err(r)
        assert await _reasons(t.id) == ["idp_error"]
    _go(body)


def test_oidc_jit_off_unknown_user(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
        idp = FakeIdP(monkeypatch)
        idp.email = f"g-{ids.h}-new@corp.example"
        _err(await _flow(c, idp, t.slug))
        assert await _reasons(t.id) == ["not_allowed"]
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(func.count()).select_from(User).where(
                User.email == idp.email))).scalar() == 0
    _go(body)


def test_oidc_jit_on_creates_viewer(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids, auto_provision=True)
        idp = FakeIdP(monkeypatch)
        idp.email = f"g-{ids.h}-new@corp.example"
        idp.claims = {"name": "New Person"}
        r = await _flow(c, idp, t.slug)
        async with AsyncSessionLocal() as db:
            u = (await db.execute(select(User).where(User.email == idp.email))).scalars().first()
            if u is not None:
                ids.users.append(int(u.id))
            assert u is not None and u.full_name == "New Person" and u.password_login_enabled is False
            m = (await db.execute(select(TenantMembership).where(TenantMembership.user_id == u.id))).scalars().all()
        assert [(x.tenant_id, x.role) for x in m] == [(t.id, "viewer")]
        assert r.headers["location"].endswith("/login#sso=ok")
    _go(body)


def test_oidc_other_tenant_user_not_attached(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids, auto_provision=True)
            other = await make_tenant(db, ids)
            u = await _member(db, ids, "elsewhere", [other])
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        assert _err(await _flow(c, idp, t.slug)) == sso_mod.SSO_NOT_ALLOWED
        async with AsyncSessionLocal() as db:
            tids = (await db.execute(select(TenantMembership.tenant_id).where(
                TenantMembership.user_id == u.id))).scalars().all()
        assert tids == [other.id]
    _go(body)


def test_oidc_multi_tenant_member_refused(monkeypatch):
    """A tenant's IdP may only sign in accounts that belong to that tenant alone (account_guard rule)."""
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            other = await make_tenant(db, ids)
            u = await _member(db, ids, "multi", [t, other])
            single = await _member(db, ids, "single", [t])
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        assert _err(await _flow(c, idp, t.slug)) == sso_mod.SSO_NOT_ALLOWED
        assert "not_allowed" in await _reasons(t.id)
        idp.email = single.email
        r = await _flow(c, idp, t.slug)
        assert r.status_code == 303 and r.headers["location"].endswith("/login#sso=ok"), r.headers["location"]
    _go(body)


def test_oidc_unverified_email_refused(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
            u = await _member(db, ids, "an", [t])
        idp = FakeIdP(monkeypatch)
        idp.email, idp.claims = u.email, {"email_verified": False}
        _err(await _flow(c, idp, t.slug))
        assert await _reasons(t.id) == ["unverified"]
    _go(body)


def test_oidc_http_authorization_endpoint_refused(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids)
        FakeIdP(monkeypatch, auth_endpoint="http://idp.example.com/authorize")
        r = await c.get(f"/api/v1/auth/oidc/{t.slug}/login")
        _err(r)
        assert "idp.example.com" not in r.headers["location"].split("#")[0]
        assert await _reasons(t.id) == ["discovery"]
    _go(body)


def test_dispatcher(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            pw = await make_tenant(db, ids)
            t = await _oidc_tenant(db, ids, member="password", guest="oidc")
        r = await c.get(f"/api/v1/auth/sso/{pw.slug}/login")
        assert "/login#sso_error=" in r.headers["location"]
        assert unquote(r.headers["location"].split("#sso_error=")[1]) == "This organization signs in with a password."
        r = await c.get(f"/api/v1/auth/sso/{pw.slug}/login?guest=1")
        assert "/guest/login#sso_error=" in r.headers["location"]
        r = await c.get(f"/api/v1/auth/sso/{t.slug}/login?guest=1")
        assert r.status_code == 303 and r.headers["location"].endswith(f"/auth/oidc/{t.slug}/login?guest=1")
        r = await c.get(f"/api/v1/auth/sso/{t.slug}/login")
        assert "#sso_error=" in r.headers["location"]
        r = await c.get("/api/v1/auth/sso/no-such-tenant/login")
        assert "#sso_error=" in r.headers["location"]
    _go(body)


def test_oidc_guest_login(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids, member="password", guest="oidc")
            case = await make_case(db, ids, t)
            e = ids.email("g")
            await make_grant(db, case, e)
        idp = FakeIdP(monkeypatch)
        idp.email = e
        r = await c.get(f"/api/v1/auth/sso/{t.slug}/login?guest=1")
        assert r.headers["location"].endswith(f"/auth/oidc/{t.slug}/login?guest=1")
        r = await _flow(c, idp, t.slug, guest=True)
        loc = r.headers["location"]
        assert r.status_code == 303 and "/guest/sso#token=" in loc, loc
        tok = unquote(loc.split("#token=", 1)[1])
        p = tokens.decode(tok, tokens.SESSION)
        assert p["amr"] == "sso" and p["sso_tenant_id"] == t.id and p["sso_proto"] == "oidc" and p["email"] == e
        assert (await c.get(f"/api/v1/guest/cases/{case.id}", headers={"Authorization": f"Bearer {tok}"})
                ).status_code == 200
        idp.email = ids.email("nogrant")
        r = await _flow(c, idp, t.slug, guest=True)
        assert f"/guest/login?t={t.slug}#sso_error=" in r.headers["location"]
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(func.count()).select_from(User).where(
                User.email.in_([e, idp.email])))).scalar() == 0
    _go(body)


def test_oidc_callback_refused_when_method_is_saml(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await _oidc_tenant(db, ids, member="oidc", guest="oidc")
            u = await _member(db, ids, "an", [t])
            e = ids.email("g")
            await make_grant(db, await make_case(db, ids, t), e)
        idp = FakeIdP(monkeypatch)
        idp.email = u.email
        m_code, m_state = await _start(c, idp, t.slug)
        g_code, g_state = await _start(c, idp, t.slug, guest=True)
        async with AsyncSessionLocal() as db:
            row = await db.get(Tenant, t.id)
            row.member_login_method = row.guest_login_method = "saml"
            await db.commit()
        _err(await _callback(c, t.slug, m_code, m_state))
        idp.email = e
        _err(await _callback(c, t.slug, g_code, g_state))
        assert await _reasons(t.id) == ["not_enabled", "not_enabled"]
        # the login route refuses up front too
        _err(await c.get(f"/api/v1/auth/oidc/{t.slug}/login"))
    _go(body)


def test_saml_acs_refused_when_member_method_is_oidc(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids, sso=True)
            t.member_login_method = "oidc"
            await db.commit()
            u = await _member(db, ids, "an", [t])
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(u.email))
        _err(await c.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x"}))
        assert await _reasons(t.id) == ["not_enabled"]
    _go(body)


def _as_admin(user, tenant_id):
    from sqlalchemy.orm.attributes import set_committed_value
    set_committed_value(user, "memberships", [])
    app.dependency_overrides[deps.require_admin] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tenant_id


def test_config_secret_write_only():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            admin = await make_user(db, ids)
        _as_admin(admin, t.id)
        r = await c.put("/api/v1/tenants/sso-config", json={
            "oidc_issuer": ISSUER, "oidc_client_id": CLIENT_ID, "oidc_client_secret": SECRET,
            "oidc_scopes": "openid email", "member_login_method": "oidc"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["has_oidc_client_secret"] is True and j["member_login_method"] == "oidc"
        assert j["oidc_redirect_uri"] == sso_mod.oidc_redirect_uri(t.slug) and "enabled" not in j
        g = (await c.get("/api/v1/tenants/sso-config")).json()
        assert g == j and SECRET not in json.dumps(g) and not any("secret" in k and k != "has_oidc_client_secret"
                                                                   for k in g)
        async with AsyncSessionLocal() as db:
            cfg = (await db.execute(select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == t.id))).scalars().one()
            assert decrypt(cfg.oidc_client_secret_enc) == SECRET and cfg.oidc_scopes == "openid email"
            assert (await db.get(Tenant, t.id)).member_login_method == "oidc"
            audits = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == t.id))).scalars().all()
        assert audits and SECRET not in repr([a.changes for a in audits])
        # empty string keeps the stored secret
        r = await c.put("/api/v1/tenants/sso-config", json={"oidc_client_secret": ""})
        assert r.status_code == 200 and r.json()["has_oidc_client_secret"] is True
        async with AsyncSessionLocal() as db:
            cfg = (await db.execute(select(TenantSSOConfig).where(TenantSSOConfig.tenant_id == t.id))).scalars().one()
            assert decrypt(cfg.oidc_client_secret_enc) == SECRET
    _go(body)


def test_config_rejects_unready_method():
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            admin = await make_user(db, ids)
        _as_admin(admin, t.id)
        url = "/api/v1/tenants/sso-config"
        assert (await c.put(url, json={"member_login_method": "oidc"})).status_code == 422
        assert (await c.put(url, json={"guest_login_method": "saml"})).status_code == 422
        assert (await c.put(url, json={"member_login_method": "bogus"})).status_code == 422
        assert (await c.put(url, json={"oidc_issuer": "http://idp.example.com"})).status_code == 422
        assert (await c.put(url, json={"oidc_scopes": "email profile"})).status_code == 422
        async with AsyncSessionLocal() as db:
            row = await db.get(Tenant, t.id)
            assert (row.member_login_method, row.guest_login_method) == ("password", "password")
        r = await c.put(url, json={"idp_entity_id": "https://idp.example/e", "idp_sso_url": "https://idp.example/sso",
                                   "idp_x509_cert": "MIIB", "guest_login_method": "saml"})
        assert r.status_code == 200 and r.json()["guest_login_method"] == "saml"
        # clearing the SAML config while guests still use it is refused
        assert (await c.put(url, json={"idp_x509_cert": ""})).status_code == 422
    _go(body)
