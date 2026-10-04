import asyncio
import secrets

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

import app.db.base  # noqa: F401  (register all models)
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.case import Case
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower, Notification
from app.models.tenant import Tenant
from app.models.user import User


async def _scenario():
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenant": None, "user": None, "case": None}
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-notif")
            u = User(email=f"notif-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([t, u])
            await db.flush()
            ids["tenant"], ids["user"] = t.id, u.id
            db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
            c = Case(title="notif-test", tenant_id=t.id)
            db.add(c)
            await db.flush()
            ids["case"] = c.id
            db.add(Notification(tenant_id=t.id, user_id=u.id, case_id=c.id, type="mention", summary="x"))
            db.add(CaseFollower(case_id=c.id, user_id=u.id, tenant_id=t.id))
            await db.commit()

            db.add(CaseFollower(case_id=ids["case"], user_id=ids["user"], tenant_id=ids["tenant"]))
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

            await db.execute(delete(Case).where(Case.id == ids["case"]))
            await db.commit()
            n = (await db.execute(select(func.count()).select_from(Notification).where(Notification.tenant_id == ids["tenant"]))).scalar()
            f = (await db.execute(select(func.count()).select_from(CaseFollower).where(CaseFollower.tenant_id == ids["tenant"]))).scalar()
            assert (n, f) == (0, 0)
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Notification).where(Notification.tenant_id == ids["tenant"]))
            await db.execute(delete(CaseFollower).where(CaseFollower.tenant_id == ids["tenant"]))
            if ids["case"]:
                await db.execute(delete(Case).where(Case.id == ids["case"]))
            await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id == ids["tenant"]))
            if ids["user"]:
                await db.execute(delete(User).where(User.id == ids["user"]))
            if ids["tenant"]:
                await db.execute(delete(Tenant).where(Tenant.id == ids["tenant"]))
            await db.commit()
            left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
        await engine.dispose()
    assert left == 0


def test_notification_models():
    asyncio.run(_scenario())
