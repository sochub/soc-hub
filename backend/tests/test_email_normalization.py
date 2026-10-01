"""Emails are stored normalized (strip + lower) and unique case-insensitively."""
import asyncio
import secrets

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.invitation import Invitation
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User

PW = "Str0ng-Passw0rd-Here"


async def _scenario(h):
    async with AsyncSessionLocal() as db:
        t = Tenant(name="email-norm-test", slug=f"email-norm-test-{h}")
        admin = User(email=f"email-norm-admin-{h}@example.test", hashed_password=get_password_hash(PW), is_active=True)
        db.add_all([t, admin])
        await db.commit()
        tid, admin_id = t.id, admin.id
    app.dependency_overrides[deps.get_current_active_user] = lambda: admin
    app.dependency_overrides[deps.require_admin] = lambda: admin
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            # users.py create
            r = await c.post("/api/v1/users/", json={"email": f"  Alice-{h}@X.com ", "password": PW, "role": "analyst"})
            assert r.status_code in (200, 201), r.text
            assert r.json()["email"] == f"alice-{h}@x.com"
            r = await c.post("/api/v1/users/", json={"email": f"ALICE-{h}@x.com", "password": PW, "role": "analyst"})
            assert r.status_code == 409, r.text

            # invitation create (new user) + accept
            r = await c.post("/api/v1/invitations/", json={"email": f"Bob-{h}@Example.COM", "role": "viewer"})
            assert r.status_code == 201, r.text
            assert r.json()["email"] == f"bob-{h}@example.com"
            r = await c.post("/api/v1/invitations/accept", json={"token": r.json()["token"], "full_name": "Bob", "password": PW})
            assert r.status_code == 200, r.text

            # invitation for an existing member in a different case is matched to her account
            r = await c.post("/api/v1/invitations/", json={"email": f"ALICE-{h}@X.COM", "role": "viewer"})
            assert r.status_code == 409 and "already a member" in r.text, r.text

        async with AsyncSessionLocal() as db:
            emails = (await db.execute(select(User.email).where(User.email.like(f"%-{h}@%")))).scalars().all()
            assert sorted(emails) == sorted([f"alice-{h}@x.com", f"bob-{h}@example.com", f"email-norm-admin-{h}@example.test"])

        # DB-level: the lower(email) unique index rejects a case-variant insert
        async with AsyncSessionLocal() as db:
            db.add(User(email=f"Alice-{h}@x.com", hashed_password="x", is_active=True))
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            uids = (await db.execute(select(User.id).where(func.lower(User.email).like(f"%-{h}@%")))).scalars().all()
            await db.execute(delete(Invitation).where(Invitation.tenant_id == tid))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(uids)))
            await db.execute(delete(User).where(User.id.in_(uids)))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()


def test_emails_normalized_and_case_insensitively_unique():
    asyncio.run(_scenario(secrets.token_hex(3)))
