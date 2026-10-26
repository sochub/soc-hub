"""Activity entries can be pinned to the incident glance (analyst+, tenant-scoped, audited)."""
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.case import TimelineEvent
from tests.guest_fixtures import client, make_case, make_tenant, run
from tests.test_case_milestones import _member


async def _event(db, case):
    ev = TimelineEvent(case_id=case.id, event_type="comment", content="checking mailbox rules")
    db.add(ev)
    await db.commit()
    return ev


def test_pin_activity():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            ev = await _event(db, c)
            _, h = await _member(db, ids, t)
            _, hv = await _member(db, ids, t, role="viewer")
        url = f"/api/v1/cases/{c.id}/timeline/{ev.id}/pin"
        async with client() as cl:
            r = await cl.patch(url, headers=h, json={"pinned": True})
            assert r.status_code == 200, r.text
            assert r.json()["pinned"] is True
            g = await cl.get(f"/api/v1/cases/{c.id}", headers=h)
            assert [e["pinned"] for e in g.json()["timeline_events"] if e["id"] == ev.id] == [True]
            r = await cl.patch(url, headers=h, json={"pinned": False})
            assert r.json()["pinned"] is False
            assert (await cl.patch(url, headers=hv, json={"pinned": True})).status_code == 403
        async with AsyncSessionLocal() as db:
            actions = (await db.execute(select(AuditLog.action).where(
                AuditLog.entity_type == "timeline_event", AuditLog.entity_id == ev.id))).scalars().all()
        assert "timeline_pinned" in actions and "timeline_unpinned" in actions
    run(body)


def test_pin_cross_tenant_404():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            _, h = await _member(db, ids, t)
            other = await make_case(db, ids, await make_tenant(db, ids))
            ev = await _event(db, other)
        async with client() as cl:
            r = await cl.patch(f"/api/v1/cases/{other.id}/timeline/{ev.id}/pin", headers=h, json={"pinned": True})
        assert r.status_code == 404
    run(body)
