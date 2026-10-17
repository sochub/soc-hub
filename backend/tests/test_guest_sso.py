import uuid
from urllib.parse import unquote

from sqlalchemy import func, select

from app.api.v1 import sso as sso_mod
from app.db.session import AsyncSessionLocal
from app.guests import tokens
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
            assert p["amr"] == "sso" and p["sso_tenant_id"] == t.id and p["email"] == e
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
