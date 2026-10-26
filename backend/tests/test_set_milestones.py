"""set_milestones service + the after-commit containment card hook."""
from datetime import datetime, timezone

import pytest

from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.services.milestones import MilestoneConflict, set_milestones
from tests.guest_fixtures import client, make_case, make_tenant, run
from tests.test_case_milestones import _member

UTC = timezone.utc
AT = datetime(2099, 10, 22, 18, 40, tzinfo=UTC)


@pytest.fixture
def delays(monkeypatch):
    from app.tasks.slack import slack_case_event_task
    calls = []
    monkeypatch.setattr(slack_case_event_task, "delay", lambda *a: calls.append(a))
    return calls


def test_put_milestones_still_works(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            _, h = await _member(db, ids, t)
        async with client() as cl:
            ok = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h,
                              json={"contained_at": "2099-10-22T18:40:00Z"})
            bad = await cl.put(f"/api/v1/cases/{c.id}/milestones", headers=h,
                               json={"first_seen_at": "2100-01-01T00:00:00Z"})
        assert ok.status_code == 200 and ok.json()["contained"]["source"] == "declared"
        assert bad.status_code == 422 and "must not be after" in bad.text
        assert len(delays) == 1 and delays[0][1] == "containment.confirmed" and delays[0][2] == c.id
    run(body)


def test_set_milestones_conflict_restores_values(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            case = await db.get(Case, c.id)
            with pytest.raises(MilestoneConflict, match="must not be after"):
                await set_milestones(db, case, {"contained_at": datetime(2000, 1, 1, tzinfo=UTC)}, None)
            assert case.contained_at is None
            await db.rollback()
        assert delays == []
    run(body)


def test_contained_set_queues_card_after_commit(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            case = await db.get(Case, c.id)
            await set_milestones(db, case, {"contained_at": AT}, 7)
            assert delays == []
            await db.commit()
        assert delays == [(t.id, "containment.confirmed", c.id, {"at": AT.isoformat(), "user_id": 7})]
    run(body)


def test_rollback_drops_queued_card(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            c = await make_case(db, ids, t)
            case = await db.get(Case, c.id)
            await set_milestones(db, case, {"contained_at": AT}, 7)
            await db.rollback()
            await db.commit()
        assert delays == []
    run(body)
