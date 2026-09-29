import asyncio
import secrets

from sqlalchemy import delete, func, select

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseStatus
from app.models.membership import TenantMembership
from app.models.slack_integration import SlackIntegration
from app.models.tenant import Tenant
from app.models.user import User
from app.services import slack_actions as sa
from app.utils.crypto import encrypt


async def _scenario(monkeypatch):
    h = secrets.token_hex(3)
    ids = {"t": [], "u": [], "c": []}
    email = {"v": None}
    ephem, resp, events = [], [], []

    async def fake_call(token, method, **kw):
        return {"user": {"profile": {"email": email["v"]}}}

    async def fake_respond(url, payload):
        resp.append(payload)

    async def fake_ephemeral(payload, text):
        ephem.append(text)

    monkeypatch.setattr(sa, "slack_call", fake_call)
    monkeypatch.setattr(sa, "respond", fake_respond)
    monkeypatch.setattr(sa, "_ephemeral", fake_ephemeral)
    monkeypatch.setattr(sa, "emit_event", lambda *a, **k: events.append((a, k)))

    async with AsyncSessionLocal() as db:
        try:
            t1 = Tenant(name="wf-test", slug=f"wf-test-{h}-a")
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-b")
            db.add_all([t1, t2])
            await db.flush()
            ids["t"] = [t1.id, t2.id]
            pw = get_password_hash(secrets.token_hex(8))

            def mk(name, **kw):
                return User(email=f"{name}-{h}@example.test", hashed_password=pw, is_active=kw.pop("is_active", True), **kw)
            analyst, inactive, viewer, other, sup = mk("Analyst"), mk("inactive", is_active=False), mk("viewer"), mk("other"), mk("sup", is_super_admin=True)
            db.add_all([analyst, inactive, viewer, other, sup])
            await db.flush()
            ids["u"] = [u.id for u in (analyst, inactive, viewer, other, sup)]
            db.add_all([
                TenantMembership(user_id=analyst.id, tenant_id=t1.id, role="analyst"),
                TenantMembership(user_id=inactive.id, tenant_id=t1.id, role="analyst"),
                TenantMembership(user_id=viewer.id, tenant_id=t1.id, role="viewer"),
                TenantMembership(user_id=other.id, tenant_id=t2.id, role="analyst"),
            ])
            db.add(SlackIntegration(tenant_id=t1.id, team_id="TX", bot_token_enc=encrypt("x"), signing_secret_enc=encrypt("y")))
            await db.commit()
            tid, aid, aemail = t1.id, analyst.id, analyst.email

            # _platform_user
            async def pu(e):
                return await sa._platform_user(db, tid, e)
            assert (await pu(f"ANALYST-{h}@Example.Test")).id == aid, "ci match"
            assert await pu(f"inactive-{h}@example.test") is None, "inactive"
            assert await pu(f"viewer-{h}@example.test") is None, "viewer"
            assert await pu(f"other-{h}@example.test") is None, "other tenant"
            assert (await pu(f"sup-{h}@example.test")) is not None, "super admin"
            assert await pu(None) is None

            async def mkcase(status=CaseStatus.OPEN):
                c = Case(title="wf-test case", tenant_id=tid, status=status)
                db.add(c)
                await db.commit()
                ids["c"].append(c.id)
                return c.id

            async def fresh(cid):
                db.expire_all()
                return (await db.execute(select(Case).where(Case.id == cid))).scalars().one()

            async def audits(cid):
                return (await db.execute(select(func.count()).select_from(AuditLog).where(
                    AuditLog.entity_type == "case", AuditLog.entity_id == cid, AuditLog.user_id == aid))).scalar()

            def payload():
                return {"user": {"id": "U1"}, "response_url": "https://hooks.slack.com/x"}

            email["v"] = aemail
            cid = await mkcase()
            await sa._handle_case_action(tid, payload(), tid, cid, "ack")
            c = await fresh(cid)
            assert c.status == CaseStatus.IN_PROGRESS and await audits(cid) == 1, "ack"
            assert events[-1][0][:2] == (tid, "case.updated") and events[-1][1]["case_id"] == cid and "depth" not in events[-1][1], "ack event"
            assert len(resp) == 1

            await sa._handle_case_action(tid, payload(), tid, cid, "assign")
            c = await fresh(cid)
            assert c.owner_id == aid and await audits(cid) == 2, "assign"
            assert len(events) == 2

            await sa._handle_case_action(tid, payload(), tid, cid, "close")
            c = await fresh(cid)
            assert c.status == CaseStatus.CLOSED and await audits(cid) == 3, "close"
            assert len(events) == 3 and len(resp) == 3

            # stale ack must not reopen; no event, no announcement
            await sa._handle_case_action(tid, payload(), tid, cid, "ack")
            c = await fresh(cid)
            assert c.status == CaseStatus.CLOSED and len(events) == 3 and len(resp) == 3, "ack reopened"
            assert ephem[-1] == f"Case #{cid} is already closed.", ephem

            resolved = await mkcase(CaseStatus.RESOLVED)
            await sa._handle_case_action(tid, payload(), tid, resolved, "ack")
            assert (await fresh(resolved)).status == CaseStatus.RESOLVED and len(events) == 3, "resolved ack"

            # no-op action: no event, no announcement
            await sa._handle_case_action(tid, payload(), tid, cid, "close")
            assert len(events) == 3 and len(resp) == 3, "noop announced"

            # unlinked user
            fresh_id = await mkcase()
            email["v"] = "nobody@example.test"
            n = len(ephem)
            await sa._handle_case_action(tid, payload(), tid, fresh_id, "close")
            assert len(ephem) == n + 1 and "isn't linked" in ephem[-1], "unlinked msg"
            assert (await fresh(fresh_id)).status == CaseStatus.OPEN and len(events) == 3, "unlinked changed case"
        finally:
            await db.rollback()
            if ids["c"]:
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "case", AuditLog.entity_id.in_(ids["c"])))
                from app.models.case import TimelineEvent
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids["c"])))
                await db.execute(delete(Case).where(Case.id.in_(ids["c"])))
            if ids["t"]:
                await db.execute(delete(SlackIntegration).where(SlackIntegration.tenant_id.in_(ids["t"])))
                await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_(ids["t"])))
            if ids["u"]:
                await db.execute(delete(AuditLog).where(AuditLog.user_id.in_(ids["u"])))
                await db.execute(delete(User).where(User.id.in_(ids["u"])))
            if ids["t"]:
                await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["t"])))
                await db.execute(delete(Tenant).where(Tenant.id.in_(ids["t"])))
            await db.commit()
    async with AsyncSessionLocal() as db:
        n = (await db.execute(select(func.count()).select_from(Tenant).where(Tenant.slug.like("wf-test-%")))).scalar()
        assert n == 0, "leftover tenants"
    await engine.dispose()


def test_case_button_authz_and_mutations(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
