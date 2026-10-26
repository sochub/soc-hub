"""SOC-bot suggestions: recording (dedupe, caps) and resolving (accept/dismiss) with REST routes."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.war_room_suggestion import WarRoomSuggestion
from app.services import war_room_suggestions as ws
from app.services.case_service import add_artifact_to_case
from app.services.war_room_extract import Candidate
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run
from tests.test_case_milestones import _member

UTC = timezone.utc


@pytest.fixture
def delays(monkeypatch):
    from app.tasks.slack import slack_case_event_task
    calls = []
    monkeypatch.setattr(slack_case_event_task, "delay", lambda *a: calls.append(a))
    return calls


def art(value, type_="ip"):
    return Candidate("artifact", {"type": type_, "value": value}, f"{type_}:{value.lower()}")


def ms(key="contained", at="2099-10-22T18:40:00+00:00"):
    return Candidate("milestone", {"key": key, "at": at}, key)


async def rec(db, case, cands, channel="C1", ts="1.1"):
    out = await ws.record_candidates(db, case, cands, channel=channel, source_ts=ts, permalink="http://p")
    await db.commit()
    return out


def test_record_dedupes_existing_artifact():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            await add_artifact_to_case(db, case=case, artifact_type=ArtifactType.DOMAIN, value="Evil.COM",
                                       description=None, user_id=None)
            await db.commit()
            out = await rec(db, case, [art("evil.com", "domain"), art("1.2.3.4")])
            assert [s.value_key for s in out] == ["ip:1.2.3.4"] and out[0].status == "pending"
    run(body)


def test_record_dedupes_dismissed_value():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            u = await make_user(db, ids)
            (s,) = await rec(db, case, [art("1.2.3.4")])
            await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=False, user=u)
            await db.commit()
            assert await rec(db, case, [art("1.2.3.4")], ts="2.2") == []
    run(body)


def test_record_caps_5_per_message():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            out = await rec(db, case, [art(f"10.0.0.{i}") for i in range(8)])
            assert len(out) == ws.MAX_PER_MESSAGE == 5
    run(body)


def test_record_caps_20_per_channel_hour():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            n = 0
            for m in range(4):
                n += len(await rec(db, case, [art(f"10.{m}.0.{i}") for i in range(5)], ts=f"{m}.0"))
            assert n == 20 == ws.MAX_PER_CHANNEL_HOUR
            assert await rec(db, case, [art("9.9.9.9")], ts="9.0") == []
            assert len(await rec(db, case, [art("9.9.9.9")], channel="C2", ts="9.1")) == 1
    run(body)


async def _artifact_count(db, case_id):
    return (await db.execute(select(func.count()).select_from(CaseArtifact).where(
        CaseArtifact.case_id == case_id))).scalar()


def test_accept_artifact_creates_and_marks_accepted(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            u = await make_user(db, ids)
            (s,) = await rec(db, case, [art("1.2.3.4")])
            s.reply_ts = "5.5"
            await db.commit()
            r = await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=True, user=u)
            await db.commit()
            assert r.status == "accepted" and r.resolved_by_user_id == u.id and r.resolved_at is not None
            assert await _artifact_count(db, case.id) == 1
            a = (await db.execute(select(Artifact).join(CaseArtifact).where(CaseArtifact.case_id == case.id))).scalar_one()
            assert a.value == "1.2.3.4" and a.description == "From Slack war room"
            log = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == t.id, AuditLog.action == "war_room_suggestion_accepted"))).scalars().all()
            assert len(log) == 1
        assert delays == [(t.id, "war_room.rerender", case.id, {"reply_ts": "5.5", "channel": "C1"})]
    run(body)


def test_accept_milestone_sets_contained_and_queues_containment_card(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            u = await make_user(db, ids)
            (s,) = await rec(db, case, [ms()])
            r = await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=True, user=u)
            await db.commit()
            assert r.status == "accepted"
            await db.refresh(case)
            assert case.contained_at == datetime(2099, 10, 22, 18, 40, tzinfo=UTC)
        assert [d[1] for d in delays] == ["containment.confirmed"]
    run(body)


def test_accept_milestone_inversion_marks_conflict(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(
                db, ids, t, detected_at=datetime(2099, 12, 1, tzinfo=UTC))).id)
            u = await make_user(db, ids)
            (s,) = await rec(db, case, [ms()])
            r = await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=True, user=u)
            await db.commit()
            assert r.status == "conflict"
            await db.refresh(case)
            assert case.contained_at is None
            assert (await db.execute(select(func.count()).select_from(AuditLog).where(
                AuditLog.tenant_id == t.id, AuditLog.action == "war_room_suggestion_conflict"))).scalar() == 1
        assert delays == []
    run(body)


def test_double_resolve_applies_once(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            u = await make_user(db, ids)
            (s,) = await rec(db, case, [art("1.2.3.4")])
            await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=True, user=u)
            await db.commit()
            again = await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=True, user=u)
            await db.commit()
            assert again.status == "accepted" and await _artifact_count(db, case.id) == 1
    run(body)


def test_resolve_unknown_or_other_tenant_raises():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            t2 = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            u = await make_user(db, ids)
            (s,) = await rec(db, case, [art("1.2.3.4")])
            with pytest.raises(LookupError):
                await ws.resolve_suggestion(db, tenant_id=t2.id, suggestion_id=s.id, accept=True, user=u)
    run(body)


def test_viewer_cannot_accept_403():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            _, h = await _member(db, ids, t, role="viewer")
            (s,) = await rec(db, case, [art("1.2.3.4")])
        async with client() as cl:
            base = f"/api/v1/cases/{case.id}/war-room/suggestions"
            assert (await cl.post(f"{base}/{s.id}/accept", headers=h)).status_code == 403
            assert (await cl.post(f"{base}/{s.id}/dismiss", headers=h)).status_code == 403
    run(body)


def test_api_list_accept_dismiss_flow(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            _, h = await _member(db, ids, t)
            a, b = await rec(db, case, [art("1.2.3.4"), art("5.6.7.8")])
        async with client() as cl:
            base = f"/api/v1/cases/{case.id}/war-room/suggestions"
            lst = await cl.get(base, headers=h)
            assert lst.status_code == 200 and {x["id"] for x in lst.json()} == {a.id, b.id}
            assert set(lst.json()[0]) == {"id", "kind", "payload", "status", "permalink", "created_at"}
            r = await cl.post(f"{base}/{a.id}/accept", headers=h)
            assert r.status_code == 200 and r.json()["status"] == "accepted"
            r = await cl.post(f"{base}/{b.id}/dismiss", headers=h)
            assert r.json()["status"] == "dismissed"
            assert (await cl.get(base, headers=h)).json() == []
    run(body)


def test_cross_tenant_suggestion_404():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t1 = await make_tenant(db, ids)
            t2 = await make_tenant(db, ids)
            c1 = await db.get(Case, (await make_case(db, ids, t1)).id)
            c2 = await make_case(db, ids, t2)
            _, h2 = await _member(db, ids, t2)
            (s,) = await rec(db, c1, [art("1.2.3.4")])
        async with client() as cl:
            # other tenant's case
            assert (await cl.get(f"/api/v1/cases/{c1.id}/war-room/suggestions", headers=h2)).status_code == 404
            assert (await cl.post(f"/api/v1/cases/{c1.id}/war-room/suggestions/{s.id}/accept", headers=h2)).status_code == 404
            # own case, but a suggestion belonging to another tenant's case
            assert (await cl.post(f"/api/v1/cases/{c2.id}/war-room/suggestions/{s.id}/accept", headers=h2)).status_code == 404
    run(body)


def test_record_skips_concurrent_duplicate_without_dropping_batch(monkeypatch):
    """A unique-constraint race on one value must not drop the rest of the batch."""
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            # a rival committed 1.1.1.1 after our dedupe read: hide it from the pre-read
            rival = WarRoomSuggestion(tenant_id=t.id, case_id=case.id, kind="artifact",
                                      payload={"type": "ip", "value": "1.1.1.1"}, value_key="ip:1.1.1.1",
                                      status="pending", slack_channel="C9", source_ts="0.1")
            db.add(rival)
            await db.commit()
            real_execute = db.execute
            n = {"i": 0}

            async def hide_existing(stmt, *a, **kw):
                res = await real_execute(stmt, *a, **kw)
                n["i"] += 1
                if n["i"] == 2:  # the `existing` pre-read
                    class Empty:
                        def all(self):
                            return []
                    return Empty()
                return res
            monkeypatch.setattr(db, "execute", hide_existing)
            out = await ws.record_candidates(db, case, [art("1.1.1.1"), art("2.2.2.2")], channel="C1",
                                             source_ts="1.1", permalink=None)
            await db.commit()
            assert [s.value_key for s in out] == ["ip:2.2.2.2"]
    run(body)
