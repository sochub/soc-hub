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
from app.escalations.service import EscalationError
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


async def _raises(code, coro):
    try:
        await coro
    except EscalationError as e:
        assert e.status_code == code, (e.status_code, e.detail)
        return
    raise AssertionError(f"expected EscalationError {code}")


async def _scenario(emitted):
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            ta = Tenant(name="esc-svc", slug=f"esc-svc-{h}")
            db.add(ta)
            await db.flush()
            ids["tenants"].append(ta.id)
            T = ta.id

            def mk(tag, name, active=True):
                return User(email=f"{tag}-{h}@example.test", full_name=name, is_active=active,
                            hashed_password=get_password_hash(pysecrets.token_hex(8)))
            names = ["l1", "l2a", "l2b", "l2c", "l3", "adm", "out", "dead"]
            us = {n: mk(n, n.upper(), active=(n != "dead")) for n in names}
            db.add_all(us.values())
            await db.flush()
            ids["users"] += [u.id for u in us.values()]
            ms = {n: TenantMembership(user_id=u.id, tenant_id=T, role="admin" if n == "adm" else "analyst")
                  for n, u in us.items()}
            db.add_all(ms.values())
            gs = {n: UserGroup(tenant_id=T, name=n) for n in ("G1", "G2", "G2b", "G3")}
            db.add_all(gs.values())
            await db.flush()
            for g, members in {"G1": ["l1"], "G2": ["l1", "l2a", "l2b", "dead"], "G2b": ["l2c"], "G3": ["l3"]}.items():
                db.add_all([UserGroupMember(group_id=gs[g].id, membership_id=ms[m].id) for m in members])
            t1 = EscalationTier(tenant_id=T, position=1, name="Tier 1")
            t2 = EscalationTier(tenant_id=T, position=2, name="Tier 2", accept_timeout_minutes=30)
            t3 = EscalationTier(tenant_id=T, position=3, name="Tier 3")
            db.add_all([t1, t2, t3])
            await db.flush()
            for t, g in ((t1, "G1"), (t2, "G2"), (t2, "G2b"), (t3, "G3")):
                db.add(EscalationTierGroup(tier_id=t.id, group_id=gs[g].id))
            cases = [Case(title=f"c{i}", tenant_id=T, owner_id=us["l1"].id) for i in range(10)]
            db.add_all(cases)
            await db.flush()
            ids["cases"] += [c.id for c in cases]
            await db.commit()
            U = {n: u.id for n, u in us.items()}
            G = {n: g.id for n, g in gs.items()}
            c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = cases
            t2_id, c8_id = t2.id, c8.id

            # snapshot shape
            snap = await esc.snapshot_policy(db, T)
            assert [t["position"] for t in snap["tiers"]] == [1, 2, 3]
            assert snap["tiers"][1]["groups"] == [{"id": G["G2"], "name": "G2"}, {"id": G["G2b"], "name": "G2b"}]
            assert set(snap["tiers"][0]) == {"id", "position", "name", "accept_timeout_minutes", "notify_email",
                                             "notify_slack", "slack_channel", "groups"}
            assert await esc.current_position(db, c1) == 1

            # (1) escalate -> pending, owner cleared, notifications, timeline, audit, stash
            before = datetime.now(timezone.utc)
            row = await esc.escalate(db, case=c1, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            assert row.status == "pending" and c1.owner_id is None
            assert row.from_tier_id == t1.id and row.from_group_id == G["G1"], "from = actor's tier-1 group"
            assert row.requested_by == U["l1"] and row.to_tier_id == t2.id and row.to_group_id == G["G2"]
            assert before + timedelta(minutes=29) < row.due_at < before + timedelta(minutes=31)
            stash = db.sync_session.info["esc_events"]
            assert [(e[1], e[2]) for e in stash] == [("case.escalated", c1.id)]
            p = stash[0][3]["escalation"]
            assert p["dedup_key"] == f"sochub-case-{c1.id}-esc-{row.id}" == esc.dedup_key(row)
            assert p["tier"] == {"position": 2, "name": "Tier 2"} and p["group"] == {"id": G["G2"], "name": "G2"}
            assert p["requested_by"]["id"] == U["l1"] and p["status"] == "pending" and p["accepted_by"] is None
            assert p["case_url"] == f"https://soc.example.com/cases/{c1.id}"
            assert set(p) == {"id", "dedup_key", "status", "tier", "group", "requested_by", "reason", "summary",
                              "requested_help", "accepted_by", "accepted_via", "case_url"}
            await db.commit()
            assert "esc_events" not in db.sync_session.info
            assert [(e[1], e[2]) for e in emitted] == [("case.escalated", c1.id)]
            assert emitted[0][3]["escalation"]["dedup_key"] == f"sochub-case-{c1.id}-esc-{row.id}"
            notes = (await db.execute(select(Notification).where(Notification.case_id == c1.id))).scalars().all()
            assert sorted(n.user_id for n in notes) == sorted([U["l2a"], U["l2b"]]), "members except actor/inactive"
            assert all(n.type == "escalated" and n.escalation_id == row.id for n in notes)
            ev = (await db.execute(select(TimelineEvent).where(TimelineEvent.case_id == c1.id))).scalars().all()
            assert [(e.event_type, e.user_id) for e in ev] == [("escalation", U["l1"])]
            assert ev[0].content.startswith("Escalated to Tier 2 · G2") and "\n" not in ev[0].content
            au = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == T, AuditLog.entity_id == c1.id,
                                                          AuditLog.entity_type == "case"))).scalars().all()
            assert [a.action for a in au] == ["case_escalated"] and au[0].changes["escalation_id"] == row.id
            assert await esc.current_position(db, c1) == 2

            # (2) validation
            await _raises(422, esc.escalate(db, case=c1, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS))
            await _raises(422, esc.escalate(db, case=c1, actor_id=U["l1"], to_tier_id=t1.id, to_group_id=G["G1"], **TEXTS))
            await _raises(422, esc.escalate(db, case=c1, actor_id=U["l1"], to_tier_id=t3.id, to_group_id=G["G2"], **TEXTS))
            await _raises(422, esc.escalate(db, case=c1, actor_id=U["l1"], to_tier_id=t3.id, to_group_id=G["G3"],
                                            **{**TEXTS, "summary": "  "}))
            await _raises(422, esc.escalate(db, case=c1, actor_id=U["l1"], to_tier_id=t3.id, to_group_id=G["G3"],
                                            **{**TEXTS, "requested_help": "x" * 4001}))

            # (3) re-escalate a pending case -> old row superseded
            emitted.clear()
            row3 = await esc.escalate(db, case=c1, actor_id=U["out"], to_tier_id=t3.id, to_group_id=G["G3"], **TEXTS)
            await db.commit()
            await db.refresh(row)
            assert row.status == "superseded" and row.closed_by == U["out"] and row.closed_at is not None
            assert row3.status == "pending" and row3.from_tier_id == t2.id
            assert [(e[1], e[3]["escalation"]["id"], e[3]["escalation"]["status"]) for e in emitted] == [
                ("case.escalation_closed", row.id, "superseded"), ("case.escalated", row3.id, "pending")]
            assert row3.due_at is None, "tier 3 has no timeout"

            # (4) accept: non-member analyst 403, admin override
            await _raises(403, esc.accept(db, case=c1, actor_id=U["out"], is_admin=False))
            r = await esc.accept(db, case=c1, actor_id=U["adm"], is_admin=True)
            assert r.id == row3.id and r.status == "accepted" and r.accepted_via == "admin_override"
            assert r.accepted_by == U["adm"] and c1.owner_id == U["adm"] and r.accepted_at is not None
            await db.commit()
            await _raises(409, esc.accept(db, case=c1, actor_id=U["l3"], is_admin=False))
            assert emitted[-1][1] == "case.escalation_closed"
            assert emitted[-1][3]["escalation"]["accepted_by"] == {"email": f"adm-{h}@example.test", "name": "ADM"}

            # (5) Review Focus 1: concurrent accept -> exactly one wins, the other 409
            r5 = await esc.escalate(db, case=c3, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            await db.commit()

            async def accept_as(uid):
                async with AsyncSessionLocal() as s:
                    case = await s.get(Case, c3.id)
                    try:
                        await esc.accept(s, case=case, actor_id=uid, is_admin=False)
                        await s.commit()
                        return "ok"
                    except EscalationError as e:
                        await s.rollback()
                        return e.status_code
            real_member_groups = esc._member_groups

            async def slow_member_groups(*a, **kw):  # hold the row lock long enough for the other accept to queue
                await asyncio.sleep(0.3)
                return await real_member_groups(*a, **kw)
            esc._member_groups = slow_member_groups
            try:
                results = await asyncio.gather(accept_as(U["l2a"]), accept_as(U["l2b"]))
            finally:
                esc._member_groups = real_member_groups
            assert sorted(results, key=str) == [409, "ok"], results
            async with AsyncSessionLocal() as s:
                won = await s.get(CaseEscalation, r5.id)
                assert won.status == "accepted" and won.accepted_via == "soc_hub"
                assert won.accepted_by in (U["l2a"], U["l2b"])
                assert (await s.get(Case, c3.id)).owner_id == won.accepted_by

            # (6) return after a 1->3 skip goes back to tier 1 and the stored from_group
            r6 = await esc.escalate(db, case=c2, actor_id=U["out"], to_tier_id=t3.id, to_group_id=G["G3"], **TEXTS)
            assert r6.from_tier_id == t1.id and r6.from_group_id == G["G1"], "fallback: tier 1's first group"
            await _raises(409, esc.return_down(db, case=c2, actor_id=U["l3"], note="more info"))
            await esc.accept(db, case=c2, actor_id=U["l3"], is_admin=False)
            await db.commit()
            emitted.clear()
            back = await esc.return_down(db, case=c2, actor_id=U["l3"], note="Need host timeline")
            await db.commit()
            await db.refresh(r6)
            assert r6.status == "returned" and r6.closed_by == U["l3"] and r6.close_note == "Need host timeline"
            assert back.status == "pending" and back.to_tier_id == t1.id and back.to_group_id == G["G1"]
            assert c2.owner_id is None and await esc.current_position(db, c2) == 1
            ret = (await db.execute(select(Notification).where(Notification.escalation_id == back.id))).scalars().all()
            assert [(n.user_id, n.type) for n in ret] == [(U["l1"], "escalation_returned")]
            assert [(e[1], e[3]["escalation"]["tier"]["position"]) for e in emitted] == [("case.escalated", 1)]
            await _raises(409, esc.return_down(db, case=c2, actor_id=U["l3"], note="again"))

            # (7) redirect
            r7 = await esc.escalate(db, case=c4, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            await db.commit()
            await _raises(422, esc.redirect(db, case=c4, actor_id=U["l2a"], to_group_id=G["G3"], note="wrong team"))
            emitted.clear()
            r7b = await esc.redirect(db, case=c4, actor_id=U["l2a"], to_group_id=G["G2b"], note="Malware team")
            await db.commit()
            await db.refresh(r7)
            assert r7.status == "redirected" and r7.closed_by == U["l2a"]
            assert r7b.status == "pending" and r7b.to_tier_id == t2.id and r7b.to_group_id == G["G2b"]
            assert r7b.policy_snapshot["tiers"] == r7.policy_snapshot["tiers"] and r7b.due_at is not None
            assert r7b.from_tier_id == r7.from_tier_id and r7b.from_group_id == r7.from_group_id
            assert [e[1] for e in emitted] == ["case.escalation_closed", "case.escalated"]
            rn = (await db.execute(select(Notification).where(Notification.escalation_id == r7b.id))).scalars().all()
            assert [n.user_id for n in rn] == [U["l2c"]]

            # (8) withdraw restores the requester as owner
            c4.owner_id = U["l2c"]  # direct writes elsewhere must not matter
            w = await esc.withdraw(db, case=c4, actor_id=U["out"], note="")
            await db.commit()
            assert w.id == r7b.id and w.status == "withdrawn" and w.closed_by == U["out"]
            assert c4.owner_id == U["l1"]
            await _raises(409, esc.withdraw(db, case=c4, actor_id=U["out"], note=""))

            # (9) on_owner_set closes as assignment and stashes case.escalation_closed
            r9 = await esc.escalate(db, case=c5, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            await db.commit()
            c5.owner_id = U["out"]
            await esc.on_owner_set(db, case=c5, new_owner_id=U["out"], actor_id=U["adm"])
            stash = db.sync_session.info["esc_events"]
            assert [(e[1], e[3]["escalation"]["accepted_via"]) for e in stash] == [("case.escalation_closed", "assignment")]
            await db.commit()
            await db.refresh(r9)
            assert (r9.status, r9.accepted_via, r9.accepted_by, r9.closed_by) == ("accepted", "assignment", U["out"], U["adm"])
            assert c5.owner_id == U["out"]
            await esc.on_owner_set(db, case=c5, new_owner_id=U["l1"], actor_id=U["adm"])  # nothing pending: no-op
            assert "esc_events" not in db.sync_session.info

            # (10) on_case_closed cancels; a vanished tier never crashes (snapshot names)
            r10 = await esc.escalate(db, case=c6, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            await db.commit()
            await db.execute(update(CaseEscalation).where(CaseEscalation.id == r10.id).values(to_tier_id=None))
            await db.commit()
            emitted.clear()
            await esc.on_case_closed(db, case=c6, actor_id=U["l1"])
            await db.commit()
            assert r10.status == "cancelled" and r10.to_tier_id is None
            assert [(e[1], e[3]["escalation"]["tier"]["name"]) for e in emitted] == [("case.escalation_closed", "Tier 2")]
            await esc.on_case_closed(db, case=c6, actor_id=U["l1"])  # no-op

            # (13) re-escalating a pending case validates against, and keeps, the chain's snapshot
            r13 = await esc.escalate(db, case=c9, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            await db.commit()
            # live edit: swap tiers 2/3, rename tier 3, move G3 off it and G2b onto it
            await db.execute(update(EscalationTier).where(EscalationTier.id == t2.id).values(position=3))
            await db.execute(update(EscalationTier).where(EscalationTier.id == t3.id).values(position=2, name="Renamed"))
            await db.execute(delete(EscalationTierGroup).where(EscalationTierGroup.tier_id == t3.id))
            db.add(EscalationTierGroup(tier_id=t3.id, group_id=G["G2b"]))
            await db.commit()
            await _raises(422, esc.escalate(db, case=c9, actor_id=U["out"], to_tier_id=t3.id, to_group_id=G["G2b"], **TEXTS))
            r13b = await esc.escalate(db, case=c9, actor_id=U["l2a"], to_tier_id=t3.id, to_group_id=G["G3"], **TEXTS)
            await db.commit()
            assert r13b.policy_snapshot["tiers"] == r13.policy_snapshot["tiers"], "chain snapshot kept"
            assert r13b.policy_snapshot["to"]["tier_name"] == "Tier 3" and r13b.policy_snapshot["to"]["position"] == 3
            assert r13b.from_tier_id == t2.id and r13b.from_group_id == G["G2"], "from resolved in the chain frame"
            await db.refresh(r13)
            assert r13.status == "superseded"
            # restore the live policy
            await db.execute(update(EscalationTier).where(EscalationTier.id == t2.id).values(position=2))
            await db.execute(update(EscalationTier).where(EscalationTier.id == t3.id).values(position=3, name="Tier 3"))
            await db.execute(delete(EscalationTierGroup).where(EscalationTierGroup.tier_id == t3.id))
            db.add(EscalationTierGroup(tier_id=t3.id, group_id=G["G3"]))
            await db.commit()

            # (14) concurrent escalates on a case with nothing pending -> one pending row, the other 409
            async def escalate_as(uid):
                async with AsyncSessionLocal() as s:
                    case = await s.get(Case, c10.id)
                    try:
                        await esc.escalate(s, case=case, actor_id=uid, to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
                        await s.commit()
                        return "ok"
                    except EscalationError as e:
                        await s.rollback()
                        return e.status_code
            esc._member_groups = slow_member_groups  # both pass validation before either inserts
            try:
                results = await asyncio.gather(escalate_as(U["l1"]), escalate_as(U["out"]))
            finally:
                esc._member_groups = real_member_groups
            assert sorted(results, key=str) == [409, "ok"], results
            pend = (await db.execute(select(CaseEscalation).where(CaseEscalation.case_id == c10.id))).scalars().all()
            assert [r.status for r in pend] == ["pending"], [r.status for r in pend]

            # (11) rolled-back session emits nothing
            emitted.clear()
            await esc.escalate(db, case=c7, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            await db.rollback()
            assert "esc_events" not in db.sync_session.info
            await db.commit()
            assert emitted == []

            # (12) no policy -> 422
            await db.execute(delete(EscalationTier).where(EscalationTier.tenant_id == T))
            c8 = await db.get(Case, c8_id)  # the rollback above expired every loaded instance
            await _raises(422, esc.escalate(db, case=c8, actor_id=U["l1"], to_tier_id=t2_id, to_group_id=G["G2"], **TEXTS))
            await db.rollback()
        finally:
            await db.rollback()
            async with AsyncSessionLocal() as s3:
                await s3.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids["cases"])))
                await s3.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
                await s3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await s3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await s3.execute(delete(User).where(User.id.in_(ids["users"])))
                await s3.commit()
    await engine.dispose()


def test_escalation_service(monkeypatch):
    emitted = []
    monkeypatch.setattr(wf_events, "emit_event", lambda tenant_id, event_type, *, case_id=None, changes=None, **kw:
                        emitted.append((tenant_id, event_type, case_id, changes)))
    monkeypatch.setattr(delivery, "_enqueue", lambda ids: None)
    asyncio.run(_scenario(emitted))


async def _scenario_final(emitted):
    """Final-review fixes: closed cases, current tier after withdraw, Slack channel gating, return to a deleted
    group, and only the one-pending constraint maps to 409."""
    from sqlalchemy.exc import IntegrityError
    from app.escalations import events as esc_events
    from app.models.case import CaseStatus

    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            ta = Tenant(name="esc-svc", slug=f"esc-svcf-{h}")
            db.add(ta)
            await db.flush()
            ids["tenants"].append(ta.id)
            T = ta.id
            us = {n: User(email=f"{n}-f-{h}@example.test", full_name=n.upper(), is_active=True,
                          hashed_password=get_password_hash(pysecrets.token_hex(8))) for n in ("l1", "l2", "l3")}
            db.add_all(us.values())
            await db.flush()
            ids["users"] += [u.id for u in us.values()]
            ms = {n: TenantMembership(user_id=u.id, tenant_id=T, role="analyst") for n, u in us.items()}
            db.add_all(ms.values())
            gs = {n: UserGroup(tenant_id=T, name=n) for n in ("G1", "G1b", "G2", "G2b", "G3")}
            db.add_all(gs.values())
            await db.flush()
            for g, m in (("G1", "l1"), ("G2", "l2"), ("G3", "l3")):
                db.add(UserGroupMember(group_id=gs[g].id, membership_id=ms[m].id))
            t1 = EscalationTier(tenant_id=T, position=1, name="Tier 1")
            # tier 2 keeps a channel but has Slack unchecked: no channel post
            t2 = EscalationTier(tenant_id=T, position=2, name="Tier 2", accept_timeout_minutes=30,
                                notify_slack=False, slack_channel="#soc-t2")
            t3 = EscalationTier(tenant_id=T, position=3, name="Tier 3", notify_slack=True, slack_channel="#soc-t3")
            db.add_all([t1, t2, t3])
            await db.flush()
            for t, g in ((t1, "G1"), (t1, "G1b"), (t2, "G2"), (t2, "G2b"), (t3, "G3")):
                db.add(EscalationTierGroup(tier_id=t.id, group_id=gs[g].id))
            cases = [Case(title=f"f{i}", tenant_id=T) for i in range(6)]
            db.add_all(cases)
            await db.flush()
            ids["cases"] += [c.id for c in cases]
            await db.commit()
            U = {n: u.id for n, u in us.items()}
            G = {n: g.id for n, g in gs.items()}
            c1, c2, c3, c4, c5, c6 = cases

            def channel_posts():
                return list(db.sync_session.info.get(esc_events._CH_KEY, []))

            # current tier after withdraw: accepted at T2 -> escalate to T3 -> withdraw -> back at T2 accepted
            r2 = await esc.escalate(db, case=c1, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            assert channel_posts() == [], "tier 2 channel set but Slack unchecked: no channel post"
            await esc.accept(db, case=c1, actor_id=U["l2"], is_admin=False)
            await db.commit()
            r3 = await esc.escalate(db, case=c1, actor_id=U["l2"], to_tier_id=t3.id, to_group_id=G["G3"], **TEXTS)
            assert channel_posts() == [r3.id], "tier 3 has Slack on and a channel"
            await esc.withdraw(db, case=c1, actor_id=U["l2"], note="")
            await db.commit()
            s = (await esc.case_summaries(db, T, [c1.id]))[c1.id]
            assert (s["id"], s["status"], s["tier_position"]) == (r2.id, "accepted", 2), s
            assert await esc.current_position(db, c1) == 2
            await _raises(422, esc.escalate(db, case=c1, actor_id=U["l2"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS))
            await esc.escalate(db, case=c1, actor_id=U["l2"], to_tier_id=t3.id, to_group_id=G["G3"], **TEXTS)
            await esc.withdraw(db, case=c1, actor_id=U["l2"], note="")
            await db.commit()
            back = await esc.return_down(db, case=c1, actor_id=U["l2"], note="Need more")  # T2 row is current
            await db.commit()
            await db.refresh(r2)
            assert r2.status == "returned" and back.status == "pending" and back.to_group_id == G["G1"]

            # closed / resolved case: escalate, redirect and return are refused
            await esc.escalate(db, case=c2, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            await esc.accept(db, case=c2, actor_id=U["l2"], is_admin=False)
            await db.commit()
            c2.status = CaseStatus.CLOSED
            await db.commit()
            await _raises(409, esc.escalate(db, case=c2, actor_id=U["l2"], to_tier_id=t3.id, to_group_id=G["G3"], **TEXTS))
            await _raises(409, esc.return_down(db, case=c2, actor_id=U["l2"], note="x"))
            await esc.escalate(db, case=c3, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
            await db.commit()
            c3.status = CaseStatus.RESOLVED  # closed without passing through on_case_closed
            await db.commit()
            await _raises(409, esc.redirect(db, case=c3, actor_id=U["l2"], to_group_id=G["G2b"], note="other team"))
            # ... and the sweep cancels its due pending row instead of climbing
            res = await esc.sweep(now=datetime.now(timezone.utc) + timedelta(minutes=31), case_ids=[c3.id, c1.id])
            assert res["cancelled"] and not res["climbed"] and not res["overdue"], res
            async with AsyncSessionLocal() as s2:
                rows = (await s2.execute(select(CaseEscalation.status).where(CaseEscalation.case_id == c3.id)
                                         .order_by(CaseEscalation.id))).scalars().all()
            assert rows == ["cancelled"], rows

            # return reopens to the from-tier's next live group when the stored one was deleted; 422 when none left
            for c in (c4, c5):
                row = await esc.escalate(db, case=c, actor_id=U["l1"], to_tier_id=t2.id, to_group_id=G["G2"], **TEXTS)
                assert row.from_group_id == G["G1"]
                await esc.accept(db, case=c, actor_id=U["l2"], is_admin=False)
            await db.commit()
            await db.execute(delete(UserGroup).where(UserGroup.id == G["G1"]))
            await db.commit()
            back = await esc.return_down(db, case=c4, actor_id=U["l2"], note="more")
            await db.commit()
            assert back.to_group_id == G["G1b"] and back.policy_snapshot["to"]["group_name"] == "G1b"
            await db.execute(delete(UserGroup).where(UserGroup.id == G["G1b"]))
            await db.commit()
            try:
                await esc.return_down(db, case=c5, actor_id=U["l2"], note="more")
                raise AssertionError("expected 422")
            except EscalationError as e:
                assert (e.status_code, e.detail) == (422, "No group left on that tier"), e.detail

            # only uq_case_escalations_one_pending maps to 409; any other integrity error propagates
            tiers = (await esc.snapshot_policy(db, T))["tiers"]
            bad = {"position": 2, "tier_name": "Tier 2", "group_id": 2**31 - 7, "group_name": None}
            try:
                await esc._open(db, c6, tiers=tiers, to=bad, to_tier_id=None, frm={"position": 1},
                                from_tier_id=None, from_group_id=None, requested_by=U["l1"],
                                reason="r", summary="s", requested_help="h")
                raise AssertionError("expected IntegrityError")
            except IntegrityError as e:
                assert "uq_case_escalations_one_pending" not in str(e.orig)
        finally:
            await db.rollback()
            async with AsyncSessionLocal() as s3:
                await s3.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids["cases"])))
                await s3.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
                await s3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await s3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await s3.execute(delete(User).where(User.id.in_(ids["users"])))
                await s3.commit()
    await engine.dispose()


def test_escalation_final_fixes(monkeypatch):
    emitted = []
    monkeypatch.setattr(wf_events, "emit_event", lambda tenant_id, event_type, *, case_id=None, changes=None, **kw:
                        emitted.append((tenant_id, event_type, case_id, changes)))
    monkeypatch.setattr(delivery, "_enqueue", lambda ids: None)
    from app.escalations import events as esc_events
    monkeypatch.setattr(esc_events, "_enqueue_channel", lambda rid: None)
    asyncio.run(_scenario_final(emitted))
