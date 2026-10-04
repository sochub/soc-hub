"""Adding an existing SSO-only account to another tenant is refused (it would lock them out of SSO)."""
import asyncio
import secrets

import httpx
from sqlalchemy import delete, func, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User


async def _scenario():
    h = secrets.token_hex(3)
    ids = {"t": [], "u": []}
    await engine.dispose()
    try:
        async with AsyncSessionLocal() as db:
            ta = Tenant(name="wf-test", slug=f"wf-test-{h}-ia")
            tb = Tenant(name="wf-test", slug=f"wf-test-{h}-ib")
            admin = User(email=f"wf-test-{h}-admin@example.com", hashed_password=get_password_hash("x" * 16), is_active=True)
            sso = User(email=f"wf-test-{h}-sso@example.com", hashed_password=get_password_hash("y" * 16),
                       is_active=True, password_login_enabled=False)
            pwd = User(email=f"wf-test-{h}-pwd@example.com", hashed_password=get_password_hash("z" * 16), is_active=True)
            db.add_all([ta, tb, admin, sso, pwd])
            await db.flush()
            ids["t"] = [ta.id, tb.id]
            ids["u"] = [admin.id, sso.id, pwd.id]
            db.add_all([TenantMembership(user_id=admin.id, tenant_id=ta.id, role="admin"),
                        TenantMembership(user_id=sso.id, tenant_id=tb.id, role="analyst"),
                        TenantMembership(user_id=pwd.id, tenant_id=tb.id, role="analyst")])
            await db.commit()
            ta_id = ta.id
        app.dependency_overrides[deps.require_admin] = lambda: admin
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ta_id
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/invitations/", json={"email": sso.email, "role": "analyst"})
            assert r.status_code == 409 and "single sign-on" in r.json()["detail"], r.text
            r = await c.post("/api/v1/invitations/", json={"email": pwd.email, "role": "analyst"})
            assert r.status_code in (200, 201) and r.json()["added_directly"], r.text
        async with AsyncSessionLocal() as db:
            n = (await db.execute(select(func.count()).select_from(TenantMembership).where(
                TenantMembership.user_id == ids["u"][1], TenantMembership.tenant_id == ta_id))).scalar()
            assert n == 0
    finally:
        app.dependency_overrides.pop(deps.require_admin, None)
        app.dependency_overrides.pop(deps.get_effective_tenant_id, None)
        async with AsyncSessionLocal() as db:
            await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_(ids["t"])))
            await db.execute(delete(User).where(User.id.in_(ids["u"])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
        await engine.dispose()


def test_invite_refuses_sso_only_account():
    asyncio.run(_scenario())
