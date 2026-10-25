import uuid
from urllib.parse import unquote

from sqlalchemy import func, select

from app.api.v1 import sso as sso_mod
from app.db.session import AsyncSessionLocal
from app.guests import tokens
from app.models.tenant import Tenant
from app.models.user import User
from tests.guest_fixtures import client, make_case, make_grant, make_tenant, run


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

    def login(self, return_to=None):
        FakeAuth.relay = return_to
        return "https://idp.example/sso?x=1"


def test_guest_sso(monkeypatch):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids, require_sso=True, sso=True)
            c = await make_case(db, ids, t)
            e = ids.email("sso")
            await make_grant(db, c, e)
        cur = {}
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(cur["e"]))
        async with client() as cl:
            cur["e"] = e
            r = await cl.get(f"/api/v1/auth/saml/{t.slug}/login?guest=1")
            assert r.status_code == 303 and FakeAuth.relay == "guest"

            r = await cl.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x", "RelayState": "guest"})
            loc = r.headers["location"]
            assert r.status_code == 303 and "/guest/sso#token=" in loc, loc
            p = tokens.decode(unquote(loc.split("#token=", 1)[1]), tokens.SESSION)
            assert p["amr"] == "sso" and p["sso_tenant_id"] == t.id and p["email"] == e and p["sso_proto"] == "saml"
            tok = unquote(loc.split("#token=", 1)[1])
            assert (await cl.get(f"/api/v1/guest/cases/{c.id}",
                                 headers={"Authorization": f"Bearer {tok}"})).status_code == 200

            cur["e"] = ids.email("nogrant")
            r = await cl.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x", "RelayState": "guest"})
            loc = r.headers["location"]
            assert "/guest/login?t=" in loc and "#sso_error=" in loc and "token=" not in loc.split("#")[1]
        async with AsyncSessionLocal() as db:
            n = (await db.execute(select(func.count()).select_from(User).where(
                func.lower(User.email).in_([e, ids.emails[-1]])))).scalar()
            assert n == 0  # never provisions a User
    run(body)


def test_guest_sso_replayed_assertion_refused(monkeypatch):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids, require_sso=True, sso=True)
            e = ids.email("replay")
            await make_grant(db, await make_case(db, ids, t), e)
        fixed = FakeAuth(e)
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: fixed)
        async with client() as cl:
            url = f"/api/v1/auth/saml/{t.slug}/acs"
            r = await cl.post(url, data={"SAMLResponse": "x", "RelayState": "guest"})
            assert "/guest/sso#token=" in r.headers["location"]
            r = await cl.post(url, data={"SAMLResponse": "x", "RelayState": "guest"})
            loc = r.headers["location"]
            assert r.status_code == 303 and "/guest/login?t=" in loc and "#sso_error=" in loc
            assert unquote(loc.split("#sso_error=", 1)[1]) == sso_mod.SSO_FAILED
    run(body)


def test_unknown_tenant_error_has_no_request_supplied_slug():
    """The retry slug comes only from a real tenant with SSO ready; the request path is never echoed back."""
    async def body(ids):
        async with client() as c:
            for path in ("/api/v1/auth/saml/no-such-tenant/login?guest=1", "/api/v1/auth/saml/Evil.Slug/login?guest=1"):
                r = await c.get(path)
                loc = r.headers["location"]
                assert r.status_code == 303 and "/guest/login#sso_error=" in loc and "?t=" not in loc
            r = await c.post("/api/v1/auth/saml/no-such-tenant/acs", data={"SAMLResponse": "x", "RelayState": "guest"})
            assert r.status_code == 303 and "?t=" not in r.headers["location"]
    run(body)


def _set_method(ids, tid, method):
    async def go():
        async with AsyncSessionLocal() as db:
            t = await db.get(Tenant, tid)
            t.guest_login_method = method
            await db.commit()
    return go()


def test_guest_link_refused_when_method_is_oidc():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            g = await make_grant(db, await make_case(db, ids, t), ids.email("lnk"))
        await _set_method(ids, t.id, "oidc")
        async with client() as cl:
            r = await cl.post("/api/v1/guest/auth/link", json={"token": tokens.issue_link(g.id)})
            assert r.status_code == 403 and r.json()["detail"]["code"] == "sso_required"
    run(body)


def test_guest_saml_refused_when_method_is_oidc(monkeypatch):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids, require_sso=True, sso=True)
            e = ids.email("oidc")
            await make_grant(db, await make_case(db, ids, t), e)
        await _set_method(ids, t.id, "oidc")
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(e))
        async with client() as cl:
            r = await cl.post(f"/api/v1/auth/saml/{t.slug}/acs", data={"SAMLResponse": "x", "RelayState": "guest"})
            loc = r.headers["location"]
            assert r.status_code == 303 and "#sso_error=" in loc and "token=" not in loc
    run(body)


def test_guest_acs_protocol_mismatch_is_audited(monkeypatch):
    import asyncio
    events = []

    async def rec(category, action, **kw):
        events.append((category, action, kw.get("outcome"), kw.get("changes")))
    monkeypatch.setattr(sso_mod, "audit_event", rec)
    t = Tenant(id=987654, slug="proto-mismatch", guest_login_method="saml")
    r = asyncio.run(sso_mod._guest_acs(None, None, t, "x@example.com", proto="oidc"))
    assert r.status_code == 303 and "/guest/login" in r.headers["location"]
    assert events == [("security", "sso.failed", "denied", {"reason": "not_enabled"})]


def test_sp_login_url_is_dispatcher(monkeypatch):
    from app.services import saml_service
    assert saml_service.sp_urls("acme")["login_url"].endswith("/api/v1/auth/sso/acme/login")
