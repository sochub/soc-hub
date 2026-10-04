import asyncio
import logging
import secrets

from sqlalchemy import delete, func, select

import app.db.base  # noqa: F401  (register all models)
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseSeverity, CaseStatus, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower, Notification
from app.models.tenant import Tenant
from app.models.user import User
from app.notifications import service
from app.services import case_service

SENTINEL = "SENTINEL-COMMENT-TEXT"


def scenario(fn):
    """Build tenant T (A analyst, B, C, V viewer), tenant X (Z), case K owned by A; run fn; clean up by id."""
    async def run():
        await engine.dispose()
        h = secrets.token_hex(3)
        ids = {"tenants": [], "users": []}
        try:
            async with AsyncSessionLocal() as db:
                t = Tenant(name="wf-test", slug=f"wf-test-{h}-trig")
                x = Tenant(name="wf-test", slug=f"wf-test-{h}-trigx")
                db.add_all([t, x])
                await db.flush()
                ids["tenants"] = [t.id, x.id]
                us = {}
                for k in "ABCVZ":
                    u = User(email=f"trig-{k.lower()}-{h}@example.test", full_name=f"User {k}",
                             hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
                    db.add(u)
                    us[k] = u
                await db.flush()
                ids["users"] = [u.id for u in us.values()]
                roles = {"A": "analyst", "B": "analyst", "C": "analyst", "V": "viewer"}
                mem = {}
                for k, r in roles.items():
                    mem[k] = TenantMembership(user_id=us[k].id, tenant_id=t.id, role=r)
                    db.add(mem[k])
                db.add(TenantMembership(user_id=us["Z"].id, tenant_id=x.id, role="analyst"))
                case = Case(title="trig", tenant_id=t.id, owner_id=us["A"].id)
                db.add(case)
                await db.commit()
                ctx = {"t": t.id, "ids": {k: u.id for k, u in us.items()}, "case": case.id, "mem": {k: m.id for k, m in mem.items()}}
                await fn(db, ctx)
        finally:
            async with AsyncSessionLocal() as db:
                tids, uids = ids["tenants"], ids["users"]
                if tids:
                    await db.execute(delete(Notification).where(Notification.tenant_id.in_(tids)))
                    await db.execute(delete(CaseFollower).where(CaseFollower.tenant_id.in_(tids)))
                    await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
                    await db.execute(delete(CaseTriageResult).where(CaseTriageResult.tenant_id.in_(tids)))
                    case_ids = (await db.execute(select(Case.id).where(Case.tenant_id.in_(tids)))).scalars().all()
                    if case_ids:
                        await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(case_ids)))
                        await db.execute(delete(Case).where(Case.id.in_(case_ids)))
                    await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_(tids)))
                if uids:
                    await db.execute(delete(User).where(User.id.in_(uids)))
                if tids:
                    await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
                await db.commit()
                left = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
            await engine.dispose()
            assert left == 0
    def test(*a):
        asyncio.run(run())
    test.__name__ = fn.__name__
    test.run = run
    return test


async def notes(db, uid, type_=None):
    q = select(Notification).where(Notification.user_id == uid)
    if type_:
        q = q.where(Notification.type == type_)
    return (await db.execute(q)).scalars().all()


async def case_of(db, ctx):
    return (await db.execute(select(Case).where(Case.id == ctx["case"]))).scalars().one()


@scenario
async def test_case_created_owner_follows_and_assigned(db, ctx):
    i = ctx["ids"]
    c, _ = await case_service.create_case_record(db, tenant_id=ctx["t"], data={"title": "t", "owner_id": i["B"]}, user_id=i["A"])
    await db.commit()
    assert await service.is_following(db, c.id, i["B"])
    assert len(await notes(db, i["B"], "assigned")) == 1
    c2, _ = await case_service.create_case_record(db, tenant_id=ctx["t"], data={"title": "t2"}, user_id=i["A"])
    await db.commit()
    assert c2.owner_id == i["A"] and await service.is_following(db, c2.id, i["A"])
    assert await notes(db, i["A"]) == []


@scenario
async def test_comment_mentions_and_followers(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await service.follow(db, case, i["B"])
    ev = await case_service.add_timeline_note(db, case=case, user_id=i["A"],
                                              content=f"see @[x](user:{i['C']}) @[y](user:{i['Z']})")
    await db.commit()
    assert [n.type for n in await notes(db, i["C"])] == ["mention"]
    assert await service.is_following(db, case.id, i["C"])
    assert [n.type for n in await notes(db, i["B"])] == ["comment"]
    assert await notes(db, i["Z"]) == []
    assert await notes(db, i["A"]) == []
    assert await service.is_following(db, case.id, i["A"])
    assert f"@[User C](user:{i['C']})" in ev.content and f"@[y](user:{i['Z']})" in ev.content


@scenario
async def test_mentioned_follower_gets_only_mention(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await service.follow(db, case, i["C"])
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"hi @[x](user:{i['C']})")
    await db.commit()
    ns = await notes(db, i["C"])
    assert [n.type for n in ns] == ["mention"]


@scenario
async def test_status_and_severity_changes(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await service.follow(db, case, i["B"])
    await service.follow(db, case, i["V"])
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=SENTINEL)
    await case_service.apply_case_update(db, case=case, user_id=i["A"],
                                         update_data={"status": CaseStatus.IN_PROGRESS, "severity": CaseSeverity.CRITICAL})
    await db.commit()
    for k in ("B", "V"):
        by = {n.type: n for n in await notes(db, i[k]) if n.type != "comment"}
        assert set(by) == {"status_change", "severity_change"}
        assert "in_progress" in by["status_change"].summary and f"#{case.id}" in by["status_change"].summary
        assert "critical" in by["severity_change"].summary
        assert all(SENTINEL not in n.summary for n in await notes(db, i[k]))


@scenario
async def test_assignment_by_workflow(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await case_service.apply_case_update(db, case=case, update_data={"owner_id": i["C"]}, user_id=None)
    await db.commit()
    ns = await notes(db, i["C"], "assigned")
    assert len(ns) == 1 and ns[0].actor_id is None
    assert await service.is_following(db, case.id, i["C"])


@scenario
async def test_automation_note_never_mentions(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await service.follow(db, case, i["B"])
    content = f"[automation] @[x](user:{i['C']})"
    ev = await case_service.add_timeline_note(db, case=case, user_id=None, content=content)
    await db.commit()
    assert ev.content == content
    assert await notes(db, i["C"]) == []
    ns = await notes(db, i["B"])
    assert [(n.type, n.actor_id) for n in ns] == [("comment", None)]


@scenario
async def test_removed_member_skipped(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await service.follow(db, case, i["B"])
    await db.commit()
    await db.execute(delete(TenantMembership).where(TenantMembership.id == ctx["mem"]["B"]))
    await db.commit()
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content="hello")
    await db.commit()
    assert await notes(db, i["B"]) == []


@scenario
async def test_deactivated_user_not_notified(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await service.follow(db, case, i["B"])
    b = (await db.execute(select(User).where(User.id == i["B"]))).scalars().one()
    b.is_active = False
    await db.commit()
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content="hello")
    await case_service.apply_case_update(db, case=case, user_id=i["A"], update_data={"owner_id": i["B"]})
    await db.commit()
    assert await notes(db, i["B"]) == []


@scenario
async def test_edit_notifies_only_new_mentions(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    ev = await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"@[x](user:{i['C']})")
    await db.commit()
    both = f"@[x](user:{i['C']}) @[y](user:{i['B']})"
    await case_service.edit_timeline_note(db, case=case, event=ev, update_data={"content": both}, user_id=i["A"])
    await db.commit()
    assert len(await notes(db, i["C"], "mention")) == 1
    assert len(await notes(db, i["B"], "mention")) == 1
    await case_service.edit_timeline_note(db, case=case, event=ev, update_data={"content": both}, user_id=i["A"])
    await db.commit()
    assert len(await notes(db, i["C"])) == 1 and len(await notes(db, i["B"])) == 1


def test_write_failure_keeps_comment(monkeypatch, caplog):
    async def fn(db, ctx):
        i = ctx["ids"]
        case = await case_of(db, ctx)

        async def boom(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(service, "_members", boom)
        with caplog.at_level(logging.DEBUG):
            ev = await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=SENTINEL)
            await db.commit()
        got = (await db.execute(select(TimelineEvent).where(TimelineEvent.id == ev.id))).scalars().one()
        assert got.content == SENTINEL
        assert (await db.execute(select(func.count()).select_from(Notification).where(Notification.tenant_id == ctx["t"]))).scalar() == 0
        lines = [r.getMessage() for r in caplog.records if "notification write failed" in r.getMessage()]
        assert len(lines) == 1 and "RuntimeError" in lines[0]
        assert all(SENTINEL not in r.getMessage() for r in caplog.records)
    asyncio.run(scenario(fn).run())


@scenario
async def test_sla_breach_notifies_owner_and_followers(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)  # owner A
    await service.follow(db, case, i["B"])
    await service.on_sla_breach(db, case, "response")
    await db.commit()
    assert len(await notes(db, i["A"], "sla_breach")) == 1
    assert len(await notes(db, i["B"], "sla_breach")) == 1
    assert await notes(db, i["C"]) == []


@scenario
async def test_external_ids_stashed(db, ctx):
    i = ctx["ids"]
    case = await case_of(db, ctx)
    await service.follow(db, case, i["B"])
    await case_service.add_timeline_note(db, case=case, user_id=i["A"], content=f"@[x](user:{i['C']})")
    await case_service.apply_case_update(db, case=case, user_id=i["A"], update_data={"status": CaseStatus.IN_PROGRESS})
    await db.commit()
    mention = (await notes(db, i["C"], "mention"))[0]
    assert db.info["notif_external"] == [mention.id]
