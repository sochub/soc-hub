"""'via' attribution: activity entries and audit rows written while an MCP client acts for a user."""
import httpx
from sqlalchemy import select

from app.api import deps
from app.audit.via import acting_via, current_via
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import TimelineEvent
from app.services import case_service
from app.utils.audit import create_audit_log
from tests.guest_fixtures import make_case, make_tenant, make_user, run


def test_note_inside_acting_via_is_tagged():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            u = await make_user(db, ids)
            case = await make_case(db, ids, t)
            with acting_via("mcp", "c1", "Claude"):
                ev = await case_service.add_timeline_note(db, case=case, content="hello", user_id=u.id)
            await db.commit()
            assert (ev.via, ev.via_client) == ("mcp", "Claude")
    run(body)


def test_audit_log_inside_acting_via_has_actor():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            u = await make_user(db, ids)
            orig = {"x": 1}
            with acting_via("mcp", "c1", "Claude"):
                row = await create_audit_log(db, "case", 1, "update", t.id, user_id=u.id, changes=orig)
            assert row.actor == "mcp:Claude"
            assert row.changes["via"] == "mcp" and row.changes["client_id"] == "c1" and row.changes["x"] == 1
            assert orig == {"x": 1}  # caller's dict untouched
            row2 = await create_audit_log(db, "case", 1, "update", t.id, user_id=u.id, actor="me")
            assert row2.actor == "me" and not row2.changes
            await db.commit()
    run(body)


def test_no_leak_after_context():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            u = await make_user(db, ids)
            case = await make_case(db, ids, t)
            with acting_via("mcp", "c1", "Claude"):
                assert current_via.get()["via"] == "mcp"
            assert current_via.get() is None
            ev = await case_service.add_timeline_note(db, case=case, content="after", user_id=u.id)
            await db.commit()
            assert ev.via is None and ev.via_client is None
    run(body)


def test_timeline_schema_exposes_via():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            u = await make_user(db, ids)
            case = await make_case(db, ids, t)
            with acting_via("mcp", "c1", "Claude"):
                await case_service.add_timeline_note(db, case=case, content="tagged", user_id=u.id)
            await db.commit()
            tid, cid = t.id, case.id
            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            app.dependency_overrides[deps.get_current_user] = lambda: u
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.get(f"/api/v1/cases/{cid}")
            assert r.status_code == 200, r.text
            tagged = [e for e in r.json()["timeline_events"] if e["content"] == "tagged"]
            assert tagged and tagged[0]["via"] == "mcp" and tagged[0]["via_client"] == "Claude"
    run(body)


def test_long_client_name_is_capped():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            u = await make_user(db, ids)
            case = await make_case(db, ids, t)
            with acting_via("mcp", "c1", "N" * 200):
                row = await create_audit_log(db, "case", 1, "update", t.id, user_id=u.id)
                ev = await case_service.add_timeline_note(db, case=case, content="long", user_id=u.id)
            await db.commit()
            assert len(row.actor) == 128
            assert len(ev.via_client) <= 200
    run(body)
