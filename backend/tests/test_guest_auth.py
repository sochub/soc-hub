from sqlalchemy import select

from app.api.v1 import guest_auth, mfa as mfa_mod
from app.core import totp
from app.db.session import AsyncSessionLocal
from app.guests import tokens
from app.main import app
from app.models.audit_log import AuditLog
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

            # register only emails a confirmation link; the account is created on confirm
            r = await c.post("/api/v1/guest/auth/register", headers=_bearer(link_session))
            assert r.status_code == 202
            assert len(sent) == 1 and sent[0][0] == e and "/guest/register#" in sent[0][1]
            reg_tok = sent[0][1].split("/guest/register#", 1)[1].split('"', 1)[0]
            sent.clear()
            async with AsyncSessionLocal() as db:
                assert (await db.execute(select(GuestAccount).where(GuestAccount.email == e))).scalars().first() is None
            assert (await c.post("/api/v1/guest/auth/register/confirm",
                                 json={"token": reg_tok, "password": "short"})).status_code == 422
            r = await c.post("/api/v1/guest/auth/register/confirm", json={"token": reg_tok, "password": PW})
            assert r.status_code == 200, r.text
            p = tokens.decode(r.json()["access_token"], tokens.SESSION)
            assert p["amr"] == "password" and p["email"] == e
            assert (await c.get("/api/v1/guest/cases", headers=_bearer(r.json()["access_token"]))).status_code == 200
            r = await c.post("/api/v1/guest/auth/register/confirm", json={"token": reg_tok, "password": PW})
            assert r.status_code == 400 and r.json()["detail"] == guest_auth.BAD_LINK  # spent once the account exists
            # an account exists now: same 202, nothing sent (no enumeration)
            assert (await c.post("/api/v1/guest/auth/register", headers=_bearer(link_session))).status_code == 202
            assert sent == []
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
            assert len(sent) == 1 and "/guest/reset#" in sent[0][1] and "/guest/reset/ey" not in sent[0][1]
            reset_tok = sent[0][1].split("/guest/reset#", 1)[1].split('"', 1)[0]
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
            g_acct = await make_grant(db, await make_case(db, ids, t), e)
        async with client() as c:
            r = await c.post("/api/v1/guest/auth/link", json={"token": tokens.issue_link(g_acct.id)})
            assert r.status_code == 403 and r.json()["detail"] == {"code": "account_login_required"}
            r = await c.post("/api/v1/guest/auth/link", json={"token": tokens.issue_link(g_sso.id)})
            assert r.status_code == 403 and r.json()["detail"] == {"code": "sso_required", "slug": t_sso.slug}

            s = (await c.post("/api/v1/guest/auth/login", json={"email": e, "password": PW})).json()["access_token"]
            setup = (await c.post("/api/v1/guest/auth/mfa/setup", headers=_bearer(s))).json()
            code = totp.totp_at(setup["secret"], int(__import__("time").time()))
            # the password is required too: a stolen session alone can't lock the owner out
            assert (await c.post("/api/v1/guest/auth/mfa/enable", json={"code": code},
                                 headers=_bearer(s))).status_code == 422
            assert (await c.post("/api/v1/guest/auth/mfa/enable", json={"code": code, "password": "Wrong-Passw0rd!"},
                                 headers=_bearer(s))).status_code == 400
            r = await c.post("/api/v1/guest/auth/mfa/enable", json={"code": code, "password": PW}, headers=_bearer(s))
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
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(AuditLog.action, AuditLog.changes).where(
                AuditLog.tenant_id == t.id, AuditLog.entity_type == "case_guest"))).all()
            acts = [a for a, ch in rows if ch.get("actor") == f"guest:{e}"]
            assert "guest_password_login" in acts and "guest_mfa_login" in acts and "guest_mfa_enabled" in acts
    run(body)


def test_register_confirm_rejects_other_tokens(monkeypatch):
    monkeypatch.setattr(email_service, "_smtp_configured", lambda: True)
    monkeypatch.setattr(email_service, "_send_email", lambda *a, **k: True)

    async def body(ids):
        _overrides()
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            e = ids.email("wp")
            g = await make_grant(db, await make_case(db, ids, t), e)
            acct = await make_account(db, ids.email("acct"))
        async with client() as c:
            for bad in (tokens.issue_link(g.id), tokens.issue_reset(acct), tokens.issue_session(e, "link"), "garbage"):
                r = await c.post("/api/v1/guest/auth/register/confirm", json={"token": bad, "password": PW})
                assert r.status_code in (400, 401), r.text
            # a register token is no session and no reset token either
            reg = tokens.issue_register(e)
            assert (await c.get("/api/v1/guest/cases", headers=_bearer(reg))).status_code == 401
            assert (await c.post("/api/v1/guest/auth/reset", json={"token": reg, "password": PW})).status_code == 400
            # a password session can't ask for a register link
            pw = tokens.issue_session(acct.email, "password", gav=0)
            assert (await c.post("/api/v1/guest/auth/register", headers=_bearer(pw))).status_code == 403
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(GuestAccount).where(GuestAccount.email == e))).scalars().first() is None
    run(body)
