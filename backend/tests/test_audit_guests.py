"""Guest activity in the audit log: attribution of guest requests and the guest sign-in events."""
import json
import secrets

import httpx
from sqlalchemy import delete, select

from app.api.v1 import guest_auth
from app.db.session import AsyncSessionLocal
from app.guests import tokens
from app.main import app
from app.models.audit_log import AuditLog
from tests.guest_fixtures import PW, make_account, make_case, make_grant, make_tenant, run, session_headers
from tests.test_guest_auth import _Throttle
from tests.test_guest_sso import FakeAuth
from app.api.v1 import sso as sso_mod


class _Ua:
    """A client whose requests carry a unique User-Agent; rows() returns exactly this run's rows."""
    def __init__(self):
        self.ua = f"audit-guest-{secrets.token_hex(4)}"

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                 headers={"User-Agent": self.ua})

    async def rows(self, **where):
        async with AsyncSessionLocal() as db:
            q = select(AuditLog).where(AuditLog.user_agent == self.ua)
            for k, v in where.items():
                q = q.where(getattr(AuditLog, k) == v)
            return list((await db.execute(q.order_by(AuditLog.id))).scalars().all())

    async def drop(self):
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.user_agent == self.ua))
            await db.commit()


def _run(inner):
    async def body(ids):
        u = _Ua()
        app.dependency_overrides[guest_auth.get_guest_throttle] = lambda: _Throttle()
        try:
            await inner(ids, u)
        finally:
            await u.drop()
    run(body)


def test_guest_comment_is_attributed_to_the_guest_and_the_grant_tenant():
    async def inner(ids, u):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            e = ids.email("contrib")
            await make_grant(db, c, e, "contributor")
        async with u.client() as cl:
            r = await cl.post(f"/api/v1/guest/cases/{c.id}/comments", json={"content": "hello"},
                              headers=session_headers(e, link_tenant_id=t.id))
            assert r.status_code == 201, r.text
        writes = await u.rows(category="write")
        assert len(writes) == 1, [(x.category, x.action) for x in await u.rows()]
        assert writes[0].actor == f"guest:{e}" and writes[0].tenant_id == t.id
    _run(inner)


def test_guest_link_login_event_success():
    async def inner(ids, u):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            e = ids.email("link")
            g = await make_grant(db, await make_case(db, ids, t), e)
        async with u.client() as cl:
            r = await cl.post("/api/v1/guest/auth/link", json={"token": tokens.issue_link(g.id)})
            assert r.status_code == 200
            r = await cl.post("/api/v1/guest/auth/link", json={"token": "garbage"})
            assert r.status_code == 401
        ev = await u.rows(action="guest.login.link")
        assert len(ev) == 2 and len(await u.rows(category="security")) == 2
        ok, bad = ev
        assert (ok.outcome, ok.actor, ok.tenant_id, ok.category) == ("success", f"guest:{e}", t.id, "security")
        assert (bad.outcome, bad.actor, bad.tenant_id, bad.changes) == ("denied", None, None, None)
    _run(inner)


def test_guest_wrong_password_logs_domain_only():
    async def inner(ids, u):
        async with AsyncSessionLocal() as db:
            e = ids.email("pw")
            await make_account(db, e)
        async with u.client() as cl:
            r = await cl.post("/api/v1/guest/auth/login", json={"email": e, "password": "Wrong-Passw0rd!"})
            assert r.status_code == 401
            r = await cl.post("/api/v1/guest/auth/login", json={"email": e, "password": PW})
            assert r.status_code == 200
        ev = await u.rows(action="guest.login.password")
        assert len(ev) == 2
        bad, ok = ev
        assert bad.outcome == "denied" and bad.changes == {"email_domain": e.split("@")[1]}
        assert bad.actor is None and bad.user_email is None and bad.tenant_id is None
        assert ok.outcome == "success" and ok.actor == f"guest:{e}" and ok.tenant_id is None
        for row in await u.rows():
            blob = json.dumps({c.name: getattr(row, c.name) for c in AuditLog.__table__.columns}, default=str)
            if row.outcome == "denied":
                assert e not in blob and "Wrong-Passw0rd" not in blob
    _run(inner)


def test_guest_sso_without_grant_is_denied_in_the_sso_tenant(monkeypatch):
    async def inner(ids, u):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids, require_sso=True, sso=True, idp_initiated=True)
            c = await make_case(db, ids, t)
            ok_email = ids.email("sso")
            await make_grant(db, c, ok_email)
        nogrant = ids.email("nogrant")
        cur = {"e": nogrant}
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: FakeAuth(cur["e"]))
        async with u.client() as cl:
            url = f"/api/v1/auth/saml/{t.slug}/acs"
            await cl.post(url, data={"SAMLResponse": "x", "RelayState": "guest"})
            cur["e"] = ok_email
            await cl.post(url, data={"SAMLResponse": "x", "RelayState": "guest"})
        ev = await u.rows(action="guest.login.sso")
        assert len(ev) == 2
        denied, ok = ev
        assert (denied.outcome, denied.tenant_id, denied.actor) == ("denied", t.id, None)
        assert denied.changes == {"email_domain": nogrant.split("@")[1]}
        assert (ok.outcome, ok.tenant_id, ok.actor) == ("success", t.id, f"guest:{ok_email}")
    _run(inner)


def test_replayed_guest_assertion_records_sso_replay(monkeypatch):
    async def inner(ids, u):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids, require_sso=True, sso=True, idp_initiated=True)
            e = ids.email("replay")
            await make_grant(db, await make_case(db, ids, t), e)
        fixed = FakeAuth(e)
        monkeypatch.setattr(sso_mod.saml_service, "make_auth", lambda slug, cfg, req: fixed)
        async with u.client() as cl:
            url = f"/api/v1/auth/saml/{t.slug}/acs"
            await cl.post(url, data={"SAMLResponse": "x", "RelayState": "guest"})
            await cl.post(url, data={"SAMLResponse": "x", "RelayState": "guest"})
        ev = await u.rows(action="sso.replay")
        assert len(ev) == 1 and ev[0].outcome == "denied" and ev[0].tenant_id == t.id
        assert ev[0].changes == {"reason": "replay"}
    _run(inner)


def test_guest_secrets_never_reach_a_row():
    """Canary: guest password and link token (in the JSON body of /guest/auth/link). The legacy
    /guest/link/<tok> path is a frontend SPA route and never reaches the backend, so it is not applicable."""
    async def inner(ids, u):
        canary_pw = "CANARY-GUEST-PW-4c1d"
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            e = ids.email("canary")
            g = await make_grant(db, await make_case(db, ids, t), e)
            await make_account(db, e)
        link = tokens.issue_link(g.id)
        async with u.client() as cl:
            await cl.post("/api/v1/guest/auth/login", json={"email": e, "password": canary_pw})
            await cl.post("/api/v1/guest/auth/link", json={"token": link})
            await cl.post("/api/v1/guest/auth/link", json={"token": link + "CANARY-LINK-9aa0"})
        rs = await u.rows()
        assert rs
        blob = json.dumps([{c.name: getattr(r, c.name) for c in AuditLog.__table__.columns} for r in rs],
                          default=str)
        for secret in (canary_pw, link, "CANARY-LINK-9aa0"):
            assert secret not in blob
    _run(inner)
