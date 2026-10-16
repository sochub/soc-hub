from sqlalchemy import select

from app.api.v1 import guest_auth, mfa as mfa_mod
from app.core import totp
from app.db.session import AsyncSessionLocal
from app.guests import tokens
from app.main import app
from app.models.case_guest import CaseGuest, GuestAccount
from app.services import email_service
from tests.guest_fixtures import PW, client, make_account, make_case, make_grant, make_tenant, run


class _Throttle:
    async def reserve(self, key, ip):
        return None

    async def reset(self, key, ip):
        return None


class _Redis:
    def __init__(self):
        self.keys = set()

    async def set(self, key, val, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def _overrides():
    r = _Redis()
    app.dependency_overrides[guest_auth.get_guest_throttle] = lambda: _Throttle()
    app.dependency_overrides[mfa_mod.get_mfa_throttle] = lambda: _Throttle()
    app.dependency_overrides[mfa_mod.get_redis] = lambda: r


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_link_register_password_login_and_reset(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "_smtp_configured", lambda: True)
    monkeypatch.setattr(email_service, "_send_email", lambda to, s, b, log_recipient=True: sent.append((to, b)) or True)

    async def body(ids):
        _overrides()
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            t2 = await make_tenant(db, ids)
            e = ids.email("reg")
            g1 = await make_grant(db, await make_case(db, ids, t), e)
            g2 = await make_grant(db, await make_case(db, ids, t2), e)
            revoked = await make_grant(db, await make_case(db, ids, t), ids.email("rv"), revoked=True)
        async with client() as c:
            assert (await c.post("/api/v1/guest/auth/link", json={"token": "garbage"})).status_code == 401
            assert (await c.post("/api/v1/guest/auth/link",
                                 json={"token": tokens.issue_link(revoked.id)})).status_code == 401
            r = await c.post("/api/v1/guest/auth/link", json={"token": tokens.issue_link(g1.id)})
            assert r.status_code == 200, r.text
            link_session = r.json()["access_token"]

            assert (await c.post("/api/v1/guest/auth/register", json={"password": "short"},
                                 headers=_bearer(link_session))).status_code == 422
            r = await c.post("/api/v1/guest/auth/register", json={"password": PW}, headers=_bearer(link_session))
            assert r.status_code == 200
            assert (await c.post("/api/v1/guest/auth/register", json={"password": PW},
                                 headers=_bearer(link_session))).status_code == 409
            async with AsyncSessionLocal() as db:
                acct = (await db.execute(select(GuestAccount).where(GuestAccount.email == e))).scalars().one()
                linked = (await db.execute(select(CaseGuest.guest_account_id).where(
                    CaseGuest.id.in_([g1.id, g2.id])))).scalars().all()
                assert linked == [acct.id, acct.id]

            # Review Focus 1: login with different casing
            r = await c.post("/api/v1/guest/auth/login", json={"email": e.upper(), "password": PW})
            assert r.status_code == 200 and "access_token" in r.json()
            pw_session = r.json()["access_token"]
            bad = await c.post("/api/v1/guest/auth/login", json={"email": e, "password": "Wrong-Passw0rd!"})
            unknown = await c.post("/api/v1/guest/auth/login", json={"email": "nobody@dept.example", "password": PW})
            assert bad.status_code == unknown.status_code == 401 and bad.json() == unknown.json()

            # forgot: identical response for known/unknown; email only for known
            assert (await c.post("/api/v1/guest/auth/forgot", json={"email": "nobody@dept.example"})).status_code == 202
            assert (await c.post("/api/v1/guest/auth/forgot", json={"email": e})).status_code == 202
            assert len(sent) == 1 and "/guest/reset/" in sent[0][1]
            reset_tok = sent[0][1].split("/guest/reset/", 1)[1].split('"', 1)[0]
            new_pw = PW + "Z9"
            assert (await c.post("/api/v1/guest/auth/reset", json={"token": reset_tok, "password": new_pw})).status_code == 204
            assert (await c.post("/api/v1/guest/auth/reset", json={"token": reset_tok, "password": new_pw})).status_code == 400
            assert (await c.get("/api/v1/guest/cases", headers=_bearer(pw_session))).status_code == 401  # tv bumped
            assert (await c.post("/api/v1/guest/auth/login", json={"email": e, "password": new_pw})).status_code == 200
    run(body)


def test_mfa_two_step_and_sso_required_link():
    async def body(ids):
        _overrides()
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            t_sso = await make_tenant(db, ids, require_sso=True, sso=True)
            e = ids.email("mfa")
            await make_grant(db, await make_case(db, ids, t), e)
            g_sso = await make_grant(db, await make_case(db, ids, t_sso), ids.email("sso"))
            await make_account(db, e)
        async with client() as c:
            r = await c.post("/api/v1/guest/auth/link", json={"token": tokens.issue_link(g_sso.id)})
            assert r.status_code == 403 and r.json()["detail"] == {"code": "sso_required", "slug": t_sso.slug}

            s = (await c.post("/api/v1/guest/auth/login", json={"email": e, "password": PW})).json()["access_token"]
            setup = (await c.post("/api/v1/guest/auth/mfa/setup", headers=_bearer(s))).json()
            code = totp.totp_at(setup["secret"], int(__import__("time").time()))
            r = await c.post("/api/v1/guest/auth/mfa/enable", json={"code": code}, headers=_bearer(s))
            assert r.status_code == 200
            assert (await c.get("/api/v1/guest/me", headers=_bearer(s))).status_code == 401  # old session dead

            r = await c.post("/api/v1/guest/auth/login", json={"email": e, "password": PW})
            assert r.json().get("mfa_required") is True and "access_token" not in r.json()
            mfa_token = r.json()["mfa_token"]
            assert (await c.post("/api/v1/guest/auth/login/mfa",
                                 json={"mfa_token": mfa_token, "code": "000000"})).status_code == 400
            code2 = totp.totp_at(setup["secret"], int(__import__("time").time()) + 30)
            r = await c.post("/api/v1/guest/auth/login/mfa", json={"mfa_token": mfa_token, "code": code2})
            assert r.status_code == 200 and "access_token" in r.json()
            r = await c.post("/api/v1/guest/auth/login/mfa", json={"mfa_token": mfa_token, "code": code2})
            assert r.status_code in (400, 401)  # replayed step / spent challenge
    run(body)
