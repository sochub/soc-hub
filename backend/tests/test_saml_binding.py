"""SAML login CSRF: the ACS only accepts a response to an AuthnRequest this browser started (request id kept
server-side, keyed by an HttpOnly cookie; InResponseTo must match; single use). Unsolicited (IdP-initiated)
responses are refused unless the tenant explicitly enables them."""
import uuid
from urllib.parse import unquote

from sqlalchemy import update

from app.api.v1 import sso as sso_mod
from app.core.session_cookie import COOKIE
from app.db.session import AsyncSessionLocal
from app.models.tenant_sso_config import TenantSSOConfig
from tests.test_web_sessions import PW, XRW, LOGIN, _call, _cookies, _go, _tenant, _user
from tests.test_tenant_deactivation import _super_admin


class SpAuth:
    """python3-saml stand-in: login() issues a request id; process_response() mimics the library's
    InResponseTo check (only enforced when both a request id and an InResponseTo exist)."""
    issued = []

    def __init__(self, email, irt=None):
        self.email, self.irt = email, irt
        self.aid = f"_{uuid.uuid4().hex}"
        self.errors, self.seen_request_id = [], "unset"

    def login(self, return_to=None):
        self.rid = f"ONELOGIN_{uuid.uuid4().hex}"
        SpAuth.issued.append(self.rid)
        return "https://idp.example/sso?SAMLRequest=x"

    def get_last_request_id(self):
        return self.rid

    def process_response(self, request_id=None):
        self.seen_request_id = request_id
        if self.irt is not None and request_id is not None and self.irt != request_id:
            self.errors = ["invalid_response"]

    def get_last_response_in_response_to(self):
        return self.irt

    def get_errors(self):
        return self.errors

    def is_authenticated(self):
        return not self.errors

    def get_last_error_reason(self):
        return "InResponseTo mismatch"

    def get_nameid(self):
        return self.email

    def get_attributes(self):
        return {}

    def get_last_assertion_id(self):
        return self.aid

    def get_last_assertion_not_on_or_after(self):
        return None


def _ok(r):
    return r.status_code == 303 and r.headers["location"].endswith("/login#sso=ok")


def _err(r):
    loc = r.headers["location"]
    return r.status_code == 303 and "#sso_error=" in loc and not any(h.startswith(f"{COOKIE}=") for h in r.headers.get_list("set-cookie"))


async def _setup(ids, allow_idp=False):
    async with AsyncSessionLocal() as db:
        t = await _tenant(db, ids, "saml", sso=True)
        u = await _user(db, ids, "a", [(t, "analyst")])
        if allow_idp:
            await db.execute(update(TenantSSOConfig).where(TenantSSOConfig.tenant_id == t.id)
                             .values(saml_allow_idp_initiated=True))
            await db.commit()
    return t, u


async def _start(c, slug, monkeypatch, auth):
    """SP-initiated login: returns the binding cookie value."""
    monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda s, cfg, req: auth)
    c.cookies.clear()
    r = await c.get(f"/api/v1/auth/saml/{slug}/login")
    assert r.status_code == 303 and r.headers["location"].startswith("https://idp.example/sso"), r.text
    raw = [h for h in r.headers.get_list("set-cookie") if h.startswith(f"{sso_mod.SAML_COOKIE}=")]
    assert raw, r.headers
    h = raw[0].lower()
    assert "httponly" in h and f"path={sso_mod._SAML_COOKIE_PATH}".lower() in h and "max-age=" in h
    return _cookies(r)[sso_mod.SAML_COOKIE].value


async def _acs(c, slug, monkeypatch, auth, bind=None, relay=None):
    monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda s, cfg, req: auth)
    c.cookies.clear()
    headers = {"Cookie": f"{sso_mod.SAML_COOKIE}={bind}"} if bind else {}
    data = {"SAMLResponse": "x", **({"RelayState": relay} if relay else {})}
    return await c.post(f"/api/v1/auth/saml/{slug}/acs", data=data, headers=headers)


def test_unsolicited_response_refused_by_default(monkeypatch):
    async def body(c, ids):
        t, u = await _setup(ids)
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email))  # IdP-initiated: no InResponseTo, no cookie
        assert _err(r), r.headers
        # an InResponseTo for some other browser's request, without our cookie: refused too
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email, irt="ONELOGIN_someone_else"))
        assert _err(r), r.headers
    _go(body)


def test_sp_initiated_round_trip_and_single_use(monkeypatch):
    async def body(c, ids):
        t, u = await _setup(ids)
        start = SpAuth(u.email)
        bind = await _start(c, t.slug, monkeypatch, start)
        auth = SpAuth(u.email, irt=start.rid)
        r = await _acs(c, t.slug, monkeypatch, auth, bind=bind)
        assert _ok(r), r.headers
        assert auth.seen_request_id == start.rid
        # the binding cookie is cleared on the way out
        assert any(h.startswith(f'{sso_mod.SAML_COOKIE}=""') or h.startswith(f"{sso_mod.SAML_COOKIE}=;")
                   for h in r.headers.get_list("set-cookie")), r.headers.get_list("set-cookie")
        # replay of the same request id (fresh assertion, same cookie) -> refused: the state was consumed
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email, irt=start.rid), bind=bind)
        assert _err(r), r.headers
    _go(body)


def test_cookie_without_matching_in_response_to_refused(monkeypatch):
    async def body(c, ids):
        t, u = await _setup(ids)
        start = SpAuth(u.email)
        bind = await _start(c, t.slug, monkeypatch, start)
        # attacker's response (their own request id) submitted into the victim's browser
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email, irt="ONELOGIN_attacker"), bind=bind)
        assert _err(r), r.headers
        # forged cookie value: no stored state
        start2 = SpAuth(u.email)
        await _start(c, t.slug, monkeypatch, start2)
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email, irt=start2.rid), bind="forged")
        assert _err(r), r.headers
    _go(body)


def test_unsolicited_with_cookie_still_refused_by_default(monkeypatch):
    async def body(c, ids):
        t, u = await _setup(ids)
        bind = await _start(c, t.slug, monkeypatch, SpAuth(u.email))
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email, irt=None), bind=bind)
        assert _err(r), r.headers
    _go(body)


def test_idp_initiated_allowed_only_when_tenant_enables_it(monkeypatch):
    async def body(c, ids):
        t, u = await _setup(ids, allow_idp=True)
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email))
        assert _ok(r), r.headers
        # a response that claims to answer a request this browser never started is still refused
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email, irt="ONELOGIN_someone_else"))
        assert _err(r), r.headers
        # SP-initiated keeps working alongside
        start = SpAuth(u.email)
        bind = await _start(c, t.slug, monkeypatch, start)
        assert _ok(await _acs(c, t.slug, monkeypatch, SpAuth(u.email, irt=start.rid), bind=bind))
    _go(body)


def test_sso_config_exposes_idp_initiated_flag_admin_only():
    async def body(c, ids):
        from tests.session_fixtures import make_web_token
        async with AsyncSessionLocal() as db:
            t = await _tenant(db, ids, "password", sso=True)
            admin = await _user(db, ids, "adm", [(t, "admin")])
            analyst = await _user(db, ids, "an", [(t, "analyst")])
        a_tok = await make_web_token(None, admin, t.id)
        n_tok = await make_web_token(None, analyst, t.id)
        r = await _call(c, "GET", "/api/v1/tenants/sso-config", headers={"Authorization": f"Bearer {a_tok}"})
        assert r.status_code == 200 and r.json()["saml_allow_idp_initiated"] is False, r.text
        r = await _call(c, "PUT", "/api/v1/tenants/sso-config", headers={"Authorization": f"Bearer {n_tok}"},
                        json={"saml_allow_idp_initiated": True})
        assert r.status_code == 403
        r = await _call(c, "PUT", "/api/v1/tenants/sso-config", headers={"Authorization": f"Bearer {a_tok}"},
                        json={"saml_allow_idp_initiated": True})
        assert r.status_code == 200 and r.json()["saml_allow_idp_initiated"] is True, r.text
    _go(body)


def test_super_admin_password_login_unaffected_by_saml(monkeypatch):
    async def body(c, ids):
        async with AsyncSessionLocal() as db:
            await _tenant(db, ids, "saml", sso=True)
            sa = await _super_admin(db, ids)
        r = await _call(c, "POST", LOGIN, headers=XRW, data={"username": sa.email, "password": PW})
        assert r.status_code == 200, r.text
    _go(body)


def test_guest_relay_comes_from_bound_state(monkeypatch):
    """A guest SP-initiated login stays a guest login: the stored state, not the unsigned RelayState, decides."""
    async def body(c, ids):
        t, u = await _setup(ids)
        start = SpAuth(u.email)
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda s, cfg, req: start)
        bind = await _start(c, t.slug, monkeypatch, start)  # member login
        # RelayState flipped to "guest" by the attacker/IdP: still processed as the member flow it was started as
        r = await _acs(c, t.slug, monkeypatch, SpAuth(u.email, irt=start.rid), bind=bind, relay="guest")
        assert _ok(r), r.headers
        assert "/guest/" not in unquote(r.headers["location"])
    _go(body)


def test_acs_without_saml_response_is_a_clean_refusal(monkeypatch):
    """The real library raises on a POST with no SAMLResponse: refused like any invalid response, cookie cleared."""
    async def body(c, ids):
        real = sso_mod.saml_service.make_auth
        t, u = await _setup(ids)
        bind = await _start(c, t.slug, monkeypatch, SpAuth(u.email))
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", real)  # the real python3-saml
        c.cookies.clear()
        r = await c.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"RelayState": "x"},
                         headers={"Cookie": f"{sso_mod.SAML_COOKIE}={bind}"})
        assert r.status_code == 303 and _err(r), r.text
        assert unquote(r.headers["location"].split("#sso_error=", 1)[1]) == sso_mod.SSO_FAILED
        assert any(h.startswith(f'{sso_mod.SAML_COOKIE}=""') for h in r.headers.get_list("set-cookie"))
    _go(body)
