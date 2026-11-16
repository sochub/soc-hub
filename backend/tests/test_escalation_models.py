import asyncio
import secrets

from sqlalchemy import delete, func, select

import app.db.base  # noqa: F401  (register all models)
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.case import Case
from app.models.escalation import CaseEscalation, EscalationTier, UserGroup, UserGroupMember
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User


async def _base(db, h):
    t = Tenant(name="esc-test", slug=f"esc-test-{h}")
    u = User(email=f"esc-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
    db.add_all([t, u])
    await db.flush()
    m = TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst")
    db.add(m)
    await db.flush()
    return t, u, m


async def _cleanup(ids):
    async with AsyncSessionLocal() as db:
        if ids.get("case"):
            await db.execute(delete(Case).where(Case.id == ids["case"]))
        for model, key in ((EscalationTier, "tier"), (UserGroup, "group")):
            if ids.get(key):
                await db.execute(delete(model).where(model.id == ids[key]))
        if ids.get("tenant"):
            await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id == ids["tenant"]))
        if ids.get("user"):
            await db.execute(delete(User).where(User.id == ids["user"]))
        if ids.get("tenant"):
            await db.execute(delete(Tenant).where(Tenant.id == ids["tenant"]))
        await db.commit()


async def _group_member_scenario():
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {}
    try:
        async with AsyncSessionLocal() as db:
            t, u, m = await _base(db, h)
            ids.update(tenant=t.id, user=u.id)
            g = UserGroup(tenant_id=t.id, name="tier1")
            db.add(g)
            await db.flush()
            ids["group"] = g.id
            db.add(UserGroupMember(group_id=g.id, membership_id=m.id))
            await db.commit()
            await db.execute(delete(TenantMembership).where(TenantMembership.id == m.id))
            await db.commit()
            n = (await db.execute(select(func.count()).select_from(UserGroupMember).where(UserGroupMember.group_id == g.id))).scalar()
            assert n == 0
    finally:
        await _cleanup(ids)


async def _tier_delete_scenario():
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {}
    try:
        async with AsyncSessionLocal() as db:
            t, u, m = await _base(db, h)
            ids.update(tenant=t.id, user=u.id)
            c = Case(title="esc-test", tenant_id=t.id)
            tier = EscalationTier(tenant_id=t.id, position=1, name="T1")
            db.add_all([c, tier])
            await db.flush()
            ids.update(case=c.id, tier=tier.id)
            e = CaseEscalation(
                tenant_id=t.id, case_id=c.id, to_tier_id=tier.id, requested_by=u.id,
                reason="r", summary="s", requested_help="h", status="pending",
                policy_snapshot={"tiers": [{"name": "T1"}]},
            )
            db.add(e)
            await db.commit()
            eid = e.id
            await db.execute(delete(EscalationTier).where(EscalationTier.id == tier.id))
            await db.commit()
            db.expire_all()
            row = (await db.execute(select(CaseEscalation).where(CaseEscalation.id == eid))).scalar_one()
            assert row.to_tier_id is None
            assert row.policy_snapshot == {"tiers": [{"name": "T1"}]}
            assert row.overdue_notified is False
    finally:
        await _cleanup(ids)


def test_group_member_cascades_with_membership():
    asyncio.run(_group_member_scenario())


def test_escalation_row_survives_tier_delete():
    asyncio.run(_tier_delete_scenario())
