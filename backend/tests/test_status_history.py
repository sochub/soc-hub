"""Correcting a case's status history (the glance's phase bar): fix or delete status changes made by mistake."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.membership import TenantMembership
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run
from tests.session_fixtures import make_web_token

UTC = timezone.utc


async def _member(db, ids, tenant, role="analyst"):
    u = await make_user(db, ids)
    db.add(TenantMembership(user_id=u.id, tenant_id=tenant.id, role=role))
    await db.commit()
    return u, {"Authorization": f"Bearer {await make_web_token(db, u, tenant.id)}"}


def test_correct_and_delete_status_changes():
    async def body(ids):
        now = datetime.now(UTC).replace(microsecond=0)
        async with AsyncSessionLocal() as db:
            t, other = await make_tenant(db, ids), await make_tenant(db, ids)
            me, h = await _member(db, ids, t)
            _, h_other_analyst = await _member(db, ids, t)
            _, h_admin = await _member(db, ids, t, role="admin")
            _, h_elsewhere = await _member(db, ids, other, role="admin")
            c = await make_case(db, ids, t, status=CaseStatus.CLOSED, created_at=now - timedelta(hours=10),
                                resolved_at=now - timedelta(hours=1))
            # closed by hand, then an MCP client re-opened and closed it again
            rows = [(8, "new", "closed", None, "\n\n**Closing note:** done"), (2, "closed", "open", "mcp", ""),
                    (1, "open", "closed", "mcp", "")]
            ev = []
            for hrs, a, b, via, tail in rows:
                e = TimelineEvent(case_id=c.id, user_id=me.id, event_type="status_change", via=via,
                                  content=f"Status changed from {a} to {b}{tail}", created_at=now - timedelta(hours=hrs))
                db.add(e)
                ev.append(e)
            note = TimelineEvent(case_id=c.id, user_id=me.id, event_type="comment", content="hi")
            db.add(note)
            await db.commit()
            ids_ = [e.id for e in ev]

        async def case_row():
            async with AsyncSessionLocal() as db:
                return await db.get(Case, c.id)

        base = f"/api/v1/cases/{c.id}/status-history"
        async with client() as cl:
            # only the person who made the change or an admin; other tenants and non-status entries are 404
            assert (await cl.delete(f"{base}/{ids_[1]}", headers=h_other_analyst)).status_code == 403
            assert (await cl.delete(f"{base}/{ids_[1]}", headers=h_elsewhere)).status_code == 404
            assert (await cl.delete(f"{base}/{note.id}", headers=h)).status_code == 404

            # delete the MCP re-open and the second close (the bar's mistake)
            r = await cl.delete(f"{base}/{ids_[1]}", headers=h)
            assert r.status_code == 200, r.text
            # without the re-open, the case has been closed since the first close
            assert r.json()["resolved_at"]["to"] == (now - timedelta(hours=8)).isoformat()
            r = await cl.delete(f"{base}/{ids_[2]}", headers=h_admin)
            assert r.status_code == 200, r.text
            c1 = await case_row()
            assert c1.status == CaseStatus.CLOSED  # the current status is never changed
            assert c1.resolved_at == now - timedelta(hours=8)  # resolved when it was really closed

            # correct the time (and statuses) of the remaining change; the closing note is kept
            fix = {"at": (now - timedelta(hours=7)).isoformat(), "from_status": "in_progress", "to_status": "closed"}
            r = await cl.put(f"{base}/{ids_[0]}", headers=h, json=fix)
            assert r.status_code == 200, r.text
            async with AsyncSessionLocal() as db:
                e = await db.get(TimelineEvent, ids_[0])
                assert e.content == "Status changed from in_progress to closed\n\n**Closing note:** done"
                assert e.created_at == now - timedelta(hours=7)
            assert (await case_row()).resolved_at == now - timedelta(hours=7)

            # bad corrections
            for bad in ({**fix, "to_status": "in_progress"}, {**fix, "from_status": "bogus"},
                        {**fix, "at": (now + timedelta(hours=1)).isoformat()},
                        {**fix, "at": (now - timedelta(hours=11)).isoformat()},
                        {**fix, "at": (now - timedelta(hours=7)).replace(tzinfo=None).isoformat()}):
                assert (await cl.put(f"{base}/{ids_[0]}", headers=h, json=bad)).status_code == 422, bad

        async with AsyncSessionLocal() as db:
            actions = (await db.execute(select(AuditLog.action).where(
                AuditLog.tenant_id == t.id, AuditLog.entity_id == c.id,
                AuditLog.action.like("status_history_%")))).scalars().all()
        # deletes, the resolved_at move the first delete caused, and the correction
        assert sorted(actions) == ["status_history_corrected", "status_history_corrected", "status_history_deleted",
                                   "status_history_deleted"]
    run(body)


def test_history_that_does_not_end_in_current_status_leaves_resolved_at():
    async def body(ids):
        now = datetime.now(UTC).replace(microsecond=0)
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            me, h = await _member(db, ids, t)
            c = await make_case(db, ids, t, status=CaseStatus.CLOSED, created_at=now - timedelta(hours=5),
                                resolved_at=now - timedelta(hours=1))
            a = TimelineEvent(case_id=c.id, user_id=me.id, event_type="status_change",
                              content="Status changed from new to closed", created_at=now - timedelta(hours=3))
            b = TimelineEvent(case_id=c.id, user_id=me.id, event_type="status_change",
                              content="Status changed from closed to open", created_at=now - timedelta(hours=2))
            db.add_all([a, b])
            await db.commit()
        async with client() as cl:
            # the generic activity delete resyncs too: history now ends closed (at -3h), matching the case
            r = await cl.delete(f"/api/v1/cases/{c.id}/timeline/{b.id}", headers=h)
            assert r.status_code == 200, r.text
            async with AsyncSessionLocal() as db:
                assert (await db.get(Case, c.id)).resolved_at == now - timedelta(hours=3)
            # a history ending in another status than the case's says nothing about resolved_at
            fix = {"at": (now - timedelta(hours=3)).isoformat(), "from_status": "new", "to_status": "open"}
            assert (await cl.put(f"/api/v1/cases/{c.id}/status-history/{a.id}", headers=h, json=fix)).status_code == 200
            async with AsyncSessionLocal() as db:
                assert (await db.get(Case, c.id)).resolved_at == now - timedelta(hours=3)
    run(body)
