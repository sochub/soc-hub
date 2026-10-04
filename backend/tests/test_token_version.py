import asyncio
import secrets

import httpx
import jwt
from sqlalchemy import delete, select

from app.core import security
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User
from app.models.membership import TenantMembership


def _run(coro):
    return asyncio.run(coro)


async def _setup():
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{h}-tv")
        u = User(email=f"wf-test-{h}-tv@example.com", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        a = User(email=f"wf-test-{h}-tva@example.com", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, u, a])
        await db.flush()
        db.add_all([TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"),
                    TenantMembership(user_id=a.id, tenant_id=t.id, role="admin")])
        await db.commit()
        return {"t": int(t.id), "u": int(u.id), "a": int(a.id)}


async def _cleanup(ids):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_([ids["u"], ids["a"]])))
        await db.execute(delete(User).where(User.id.in_([ids["u"], ids["a"]])))
        await db.execute(delete(Tenant).where(Tenant.id == ids["t"]))
        await db.commit()


async def _user(uid):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.id == uid))).scalars().one()


def _scenario(body):
    async def go():
        await engine.dispose()
        ids = await _setup()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await body(c, ids)
        finally:
            await _cleanup(ids)
            await engine.dispose()
    _run(go())


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_issued_token_works():
    async def body(c, ids):
        u = await _user(ids["u"])
        r = await c.get("/api/v1/users/me", headers=_hdr(security.issue_access_token(u, ids["t"])))
        assert r.status_code == 200
    _scenario(body)


def test_version_mismatch_rejected():
    async def body(c, ids):
        u = await _user(ids["u"])
        tok = security.issue_access_token(u, ids["t"])
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(User).where(User.id == ids["u"]))).scalars().one()
            security.bump_token_version(row)
            await db.commit()
        assert (await c.get("/api/v1/users/me", headers=_hdr(tok))).status_code == 401
    _scenario(body)


def test_purpose_token_rejected():
    async def body(c, ids):
        u = await _user(ids["u"])
        r = await c.get("/api/v1/users/me", headers=_hdr(security.issue_mfa_challenge(u)))
        assert r.status_code == 401
    _scenario(body)


def test_tokens_carry_tv():
    async def body(c, ids):
        u = await _user(ids["u"])
        payload = jwt.decode(security.issue_access_token(u, ids["t"]), settings.SECRET_KEY,
                             algorithms=[settings.ALGORITHM])
        assert payload["tv"] == u.token_version
        assert security.decode_mfa_challenge(security.issue_mfa_challenge(u))["purpose"] == "mfa"
    _scenario(body)


def test_deactivate_bumps_version():
    async def body(c, ids):
        u, a = await _user(ids["u"]), await _user(ids["a"])
        old = security.issue_access_token(u, ids["t"])
        r = await c.put(f"/api/v1/users/{ids['u']}/deactivate",
                        headers=_hdr(security.issue_access_token(a, ids["t"])))
        assert r.status_code == 200, r.text
        assert (await c.get("/api/v1/users/me", headers=_hdr(old))).status_code == 401
    _scenario(body)
