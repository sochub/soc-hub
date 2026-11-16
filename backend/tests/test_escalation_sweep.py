import asyncio
import secrets as pysecrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

import app.notifications.delivery as delivery
import app.workflows.events as wf_events
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
import app.main  # noqa: F401  (registers every model mapper)
from app.escalations import service as esc
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.escalation import (
    CaseEscalation, EscalationTier, EscalationTierGroup, UserGroup, UserGroupMember,
)
from app.models.membership import TenantMembership
from app.models.notification import Notification
from app.models.tenant import Tenant
from app.models.user import User

TEXTS = dict(reason="Lateral movement", summary="Seen on 3 hosts", requested_help="Contain and scope")


async def _tenant(db, h, tag, ids, ncases):
    t = Tenant(name=tag, slug=f"{tag}-{h}")
    db.add(t)
    await db.flush()
    ids["tenants"].append(t.id)
    us = {}
    for n in ("l1", "l2", "l3", "l3b"):
        us[n] = User(email=f"{tag}-{n}-{h}@example.test", full_name=n, is_active=True,
                     hashed_password=get_password_hash(pysecrets.token_hex(8)))
    db.add_all(us.values())
    await db.flush()
    ids["users"] += [u.id for u in us.values()]
    ms = {n: TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst") for n, u in us.items()}
    db.add_all(ms.values())
    gs = {n: UserGroup(tenant_id=t.id, name=n) for n in ("G1", "G2", "G3", "G3b")}
    db.add_all(gs.values())
    await db.flush()
    for g, m in (("G1", "l1"), ("G2", "l2"), ("G3", "l3"), ("G3b", "l3b")):
        db.add(UserGroupMember(group_id=gs[g].id, membership_id=ms[m].id))
    tiers = [EscalationTier(tenant_id=t.id, position=1, name="Tier 1"),
             EscalationTier(tenant_id=t.id, position=2, name="Tier 2", accept_timeout_minutes=30),
             EscalationTier(tenant_id=t.id, position=3, name="Tier 3", accept_timeout_minutes=60)]
    db.add_all(tiers)
    await db.flush()
    for tier, g in ((tiers[0], "G1"), (tiers[1], "G2"), (tiers[2], "G3"), (tiers[2], "G3b")):
        db.add(EscalationTierGroup(tier_id=tier.id, group_id=gs[g].id))
    cases = [Case(title=f"c{i}", tenant_id=t.id, owner_id=us["l1"].id) for i in range(ncases)]
    db.add_all(cases)
    await db.flush()
    ids["cases"] += [c.id for c in cases]
    return t, {n: u.id for n, u in us.items()}, {n: g.id for n, g in gs.items()}, tiers, cases


async def _rows(case_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(CaseEscalation).where(CaseEscalation.case_id == case_id)
                                .order_by(CaseEscalation.id))).scalars().all()


async def _overdue_notes(user_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(Notification).where(
            Notification.user_id == user_id, Notification.type == "escalation_overdue"))).scalars().all()


async def _scenario(emitted):
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    await engine.dispose()
    try:
        async with AsyncSessionLocal() as db:
            t, U, G, tiers, cases = await _tenant(db, h, "esw", ids, 7)
            tb, UB, GB, tiersb, casesb = await _tenant(db, h, "eswb", ids, 1)
            await db.commit()
            T, TB = t.id, tb.id
            t2, t3, tb2 = tiers[1].id, tiers[2].id, tiersb[1].id
            c1, c2, c3, c4, c5, c6, c7 = [c.id for c in cases]
            cb = casesb[0].id
            for cid, tier_id, gid, who in ((c1, t2, G["G2"], U["l1"]), (c2, t2, G["G2"], U["l1"]),
                                           (c3, t2, G["G2"], U["l1"]), (c5, t2, G["G2"], U["l1"]),
                                           (c6, t2, G["G2"], U["l1"]), (c7, t3, G["G3"], U["l1"])):
                await esc.escalate(db, case=await db.get(Case, cid), actor_id=who, to_tier_id=tier_id,
                                   to_group_id=gid, **TEXTS)
            await esc.escalate(db, case=await db.get(Case, cb), actor_id=UB["l1"], to_tier_id=tb2,
                               to_group_id=GB["G2"], **TEXTS)
            await db.commit()
            # the c7 row sits on the last tier (timeout 60)
            await db.execute(update(CaseEscalation).where(CaseEscalation.case_id == c3).values(due_at=None))
            await db.commit()
        real = datetime.now(timezone.utc)
        mine = [c1, c2, c3, c4, c5, c6, c7, cb]

        # not yet due -> untouched; no timeout (due_at NULL) -> never climbs
        emitted.clear()
        assert await esc.sweep(now=real + timedelta(minutes=10), case_ids=mine) == {"climbed": [], "overdue": [], "cancelled": []}
        assert emitted == []
        assert [r.status for r in await _rows(c2)] == ["pending"]

        # (1) overdue tier-2 row climbs to tier 3 (not the deactivated tenant, not the no-timeout case)
        async with AsyncSessionLocal() as db:
            await db.execute(update(Tenant).where(Tenant.id == TB).values(is_active=False))
            await db.commit()
        old1 = (await _rows(c1))[0]
        res = await esc.sweep(now=real + timedelta(minutes=31), case_ids=[c1, c3, cb])
        assert res == {"climbed": [old1.id], "overdue": [], "cancelled": []}, res
        r1 = await _rows(c1)
        assert [r.status for r in r1] == ["climbed", "pending"]
        assert r1[0].closed_by is None and r1[0].close_note == "not accepted within 30 min"
        new = r1[1]
        assert new.policy_snapshot["to"]["position"] == 3 and new.to_group_id == G["G3"] and new.to_tier_id == t3
        assert new.requested_by == U["l1"] and new.reason == TEXTS["reason"] and new.summary == TEXTS["summary"]
        assert new.requested_help == TEXTS["requested_help"]
        assert new.policy_snapshot["tiers"] == old1.policy_snapshot["tiers"]
        assert new.policy_snapshot["from"]["position"] == 2 and new.from_group_id == G["G2"]
        assert abs((new.due_at - (datetime.now(timezone.utc) + timedelta(minutes=60))).total_seconds()) < 120
        assert [(e[1], e[2]) for e in emitted] == [("case.escalation_closed", c1), ("case.escalated", c1)]
        assert emitted[0][3]["escalation"]["status"] == "climbed"
        async with AsyncSessionLocal() as db:
            audit = (await db.execute(select(AuditLog.action).where(AuditLog.entity_id == c1,
                                                                    AuditLog.tenant_id == T))).scalars().all()
            assert "case_escalation_climbed" in audit
            got = (await db.execute(select(Notification).where(Notification.user_id == U["l3"],
                                                               Notification.case_id == c1))).scalars().all()
            assert [n.type for n in got] == ["escalated"]
            assert (await db.get(Case, c1)).owner_id is None
        assert [r.status for r in await _rows(c3)] == ["pending"]

        # (5) deactivated tenant: nothing climbs, nothing is notified
        assert [r.status for r in await _rows(cb)] == ["pending"]
        assert await _overdue_notes(UB["l2"]) == [] and await _overdue_notes(UB["l3"]) == []

        # (2) last tier: one overdue notification, then silence
        emitted.clear()
        res = await esc.sweep(now=real + timedelta(minutes=61), case_ids=[c7])
        assert res["climbed"] == [] and len(res["overdue"]) == 1
        assert [n.case_id for n in await _overdue_notes(U["l3"])] == [c7]
        assert [r.status for r in await _rows(c7)] == ["pending"] and (await _rows(c7))[0].overdue_notified
        assert emitted == []
        assert await esc.sweep(now=real + timedelta(minutes=120), case_ids=[c7]) == {"climbed": [], "overdue": [], "cancelled": []}
        assert len(await _overdue_notes(U["l3"])) == 1

        # (3) policy edited after escalating: climb uses the snapshot and skips a vanished group
        async with AsyncSessionLocal() as db:
            await db.execute(delete(EscalationTierGroup).where(EscalationTierGroup.tier_id.in_([t2, t3])))
            await db.execute(delete(EscalationTier).where(EscalationTier.id.in_([t2, t3])))
            await db.execute(delete(UserGroup).where(UserGroup.id == G["G3"]))
            await db.commit()
        res = await esc.sweep(now=real + timedelta(minutes=31), case_ids=[c5])
        assert len(res["climbed"]) == 1
        r5 = await _rows(c5)
        assert r5[1].policy_snapshot["to"]["position"] == 3 and r5[1].to_group_id == G["G3b"]
        assert r5[1].to_tier_id is None and r5[1].policy_snapshot["to"]["group_name"] == "G3b"
        # ...and with every higher group gone, the row is treated as last tier (overdue once)
        async with AsyncSessionLocal() as db:
            await db.execute(delete(UserGroup).where(UserGroup.id == G["G3b"]))
            await db.commit()
        res = await esc.sweep(now=real + timedelta(minutes=31), case_ids=[c6])
        assert res["climbed"] == [] and len(res["overdue"]) == 1
        assert (await _rows(c6))[0].status == "pending"
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Notification).where(Notification.tenant_id.in_(ids["tenants"])))
            await s.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids["cases"])))
            await s.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
            await s.execute(delete(Case).where(Case.id.in_(ids["cases"])))
            await s.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
            await s.execute(delete(User).where(User.id.in_(ids["users"])))
            await s.commit()
    await engine.dispose()


def test_escalation_sweep(monkeypatch):
    emitted = []
    monkeypatch.setattr(wf_events, "emit_event", lambda tenant_id, event_type, *, case_id=None, changes=None, **kw:
                        emitted.append((tenant_id, event_type, case_id, changes)))
    monkeypatch.setattr(delivery, "_enqueue", lambda ids: None)
    asyncio.run(_scenario(emitted))
