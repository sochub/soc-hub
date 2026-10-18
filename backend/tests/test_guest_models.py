import pytest
from sqlalchemy.exc import IntegrityError

from app.db.session import AsyncSessionLocal
from app.models.case import TimelineEvent
from tests.guest_fixtures import make_case, make_grant, make_tenant, run


def test_grant_unique_per_case_email_and_timeline_guest_email():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            assert t.guest_domain_allowlist == ["dept.example"] and t.guest_require_sso is False
            c = await make_case(db, ids, t)
            g = await make_grant(db, c, ids.email("a"))
            ev = TimelineEvent(case_id=c.id, guest_id=g.id, event_type="guest_comment", content="hi",
                               shared_with_guests=True)
            db.add(ev)
            await db.commit()
            ev_id = ev.id
        async with AsyncSessionLocal() as db:
            ev = await db.get(TimelineEvent, ev_id)
            assert ev.guest_email == g.email and ev.shared_with_guests is True
            with pytest.raises(IntegrityError):
                await make_grant(db, c, g.email)
            await db.rollback()
    run(body)
