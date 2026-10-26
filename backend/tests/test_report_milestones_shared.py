"""The report shows the case's milestones; it no longer stores its own."""
from datetime import datetime, timezone

from sqlalchemy import delete

from app.db.session import AsyncSessionLocal
from app.models.case_report import CaseReport
from tests.guest_fixtures import client, make_case, make_tenant, run
from tests.test_case_milestones import _member

UTC = timezone.utc


async def _drop_reports(cid):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(CaseReport).where(CaseReport.case_id == cid))
        await db.commit()


def test_report_uses_case_milestones():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t, contained_at=datetime(2099, 1, 2, tzinfo=UTC))
            _, h = await _member(db, ids, t)
        try:
            async with client() as cl:
                r = await cl.get(f"/api/v1/cases/{c.id}/report", headers=h)
            assert r.status_code == 200, r.text
            ms = r.json()["milestones"]
            assert ms["contained"]["source"] == "declared"
            assert ms["contained"]["at"].startswith("2099-01-02")
        finally:
            await _drop_reports(c.id)
    run(body)


def test_report_put_ignores_milestones():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            _, h = await _member(db, ids, t)
        try:
            async with client() as cl:
                r = await cl.put(f"/api/v1/cases/{c.id}/report", headers=h,
                                 json={"tlp": "amber", "contained_at": "2099-01-02T00:00:00Z"})
            assert r.status_code == 200, r.text
            from app.models.case import Case
            async with AsyncSessionLocal() as db:
                assert (await db.get(Case, c.id)).contained_at is None
        finally:
            await _drop_reports(c.id)
    run(body)
