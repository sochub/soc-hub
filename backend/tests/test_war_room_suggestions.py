"""SOC-bot suggestions: recording (dedupe, caps) and resolving (accept/dismiss) with REST routes."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
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
    return Candidate("milestone", {"key": key, "at": at}, f"{key}:{at}")


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


def test_overflow_is_stored_and_reply_shows_5_plus_milestones():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            out = await rec(db, case, [art(f"10.0.0.{i}") for i in range(8)] + [ms()])
            assert len(out) == 9 and all(s.status == "pending" for s in out)
            shown, more = ws.split_for_reply(out)
            assert [s.kind for s in shown] == ["artifact"] * ws.MAX_PER_MESSAGE + ["milestone"] and more == 3
            out[7].status = "dismissed"
            assert ws.split_for_reply(out)[1] == 2
    run(body)


def test_hourly_cap_ignores_dismissed_and_milestones_have_their_own_budget():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            u = await make_user(db, ids)
            out = await rec(db, case, [art(f"10.0.0.{i}") for i in range(25)] + [ms()])
            assert [s.kind for s in out].count("artifact") == ws.MAX_PER_CHANNEL_HOUR
            assert out[-1].kind == "milestone"   # a flood of indicators does not crowd the milestone out
            assert await rec(db, case, [art("9.9.9.9")], ts="2.2") == []
            await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=out[0].id, accept=False, user=u)
            await db.commit()
            assert len(await rec(db, case, [art("9.9.9.9")], ts="3.3")) == 1   # the dismissed one freed a slot
    run(body)


def test_domain_and_its_root_url_are_the_same_value():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            for type_, value in ((ArtifactType.DOMAIN, "Evil.COM"), (ArtifactType.URL, "https://Bad.net/")):
                await add_artifact_to_case(db, case=case, artifact_type=type_, value=value, description=None, user_id=None)
            await db.commit()
            out = await rec(db, case, [art("http://evil.com", "url"), art("http://EVIL.com/", "url"), art("bad.net", "domain"),
                                       art("http://evil.com/payload", "url"), art("https://bad.net/", "url")])
            assert [s.value_key for s in out] == ["url:http://evil.com/payload"]
    run(body)


def test_new_time_supersedes_pending_milestone(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            u = await make_user(db, ids)
            (first,) = await rec(db, case, [ms(at="2099-10-22T18:40:00+00:00")])
            first.reply_ts = "5.5"
            await db.commit()
            assert await rec(db, case, [ms(at="2099-10-22T18:40:00+00:00")], ts="2.2") == []   # same time: nothing new
            (second,) = await rec(db, case, [ms(at="2099-10-22T18:55:00+00:00")], ts="3.3")
            await db.refresh(first)
            assert first.status == "superseded" and second.status == "pending"
            assert delays == [(t.id, "war_room.rerender", case.id, {"reply_ts": "5.5", "channel": "C1"})]
            # another milestone is untouched; a time equal to the declared one is not suggested
            (other,) = await rec(db, case, [ms("detected", "2099-10-22T18:00:00+00:00")], ts="4.4")
            await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=second.id, accept=True, user=u)
            await db.commit()
            await db.refresh(other)
            assert other.status == "pending" and case.contained_at == datetime(2099, 10, 22, 18, 55, tzinfo=UTC)
            assert await rec(db, case, [ms(at="2099-10-22T19:55:00+01:00")], ts="5.5") == []
    run(body)


def test_resolve_many_reports_conflict_and_applies_the_rest(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t, detected_at=datetime(2099, 12, 1, tzinfo=UTC))).id)
            u = await make_user(db, ids)
            a, b, m = await rec(db, case, [art("1.2.3.4"), art("5.6.7.8"), ms()])
            for s in (a, b, m):
                s.reply_ts = "5.5"
            await db.commit()
            res = await ws.resolve_many(db, tenant_id=t.id, suggestion_ids=[m.id, a.id, b.id, 10**9], accept=True, user=u)
            await db.commit()
            assert [(s.status, applied, bool(why)) for s, applied, why in res] == [
                ("accepted", True, False), ("accepted", True, False), ("pending", True, True)]
            assert await _artifact_count(db, case.id) == 2
        assert [d[1] for d in delays] == ["war_room.rerender"]   # one redraw for the whole reply
    run(body)


def test_api_bulk_accept_and_dismiss(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t, detected_at=datetime(2099, 12, 1, tzinfo=UTC))).id)
            other = await db.get(Case, (await make_case(db, ids, t)).id)
            _, h = await _member(db, ids, t)
            _, viewer = await _member(db, ids, t, role="viewer")
            a, b, m = await rec(db, case, [art("1.2.3.4"), art("5.6.7.8"), ms()])
            (foreign,) = await rec(db, other, [art("9.9.9.9")], channel="C2")
        async with client() as cl:
            base = f"/api/v1/cases/{case.id}/war-room/suggestions"
            body_ = {"ids": [a.id, m.id, foreign.id], "accept": True}
            assert (await cl.post(f"{base}/bulk", json=body_, headers=viewer)).status_code == 403
            assert (await cl.post(f"{base}/bulk", json={"ids": [], "accept": True}, headers=h)).status_code == 422
            r = await cl.post(f"{base}/bulk", json=body_, headers=h)
            assert r.status_code == 200, r.text
            got = {x["id"]: x for x in r.json()}
            assert set(got) == {a.id, m.id}   # another case's suggestion is ignored
            assert got[a.id]["status"] == "accepted"
            assert got[m.id]["status"] == "pending" and "must not be after" in got[m.id]["payload"]["conflict"]
            r = await cl.post(f"{base}/bulk", json={"ids": [b.id, m.id], "accept": False}, headers=h)
            assert {x["status"] for x in r.json()} == {"dismissed"}
            assert (await cl.get(base, headers=h)).json() == []
        async with AsyncSessionLocal() as db:
            assert (await db.get(WarRoomSuggestion, foreign.id)).status == "pending"
            assert await _artifact_count(db, case.id) == 1
    run(body)


def test_api_shows_current_declared_milestone():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t, contained_at=datetime(2099, 10, 22, 17, 0, tzinfo=UTC))).id)
            _, h = await _member(db, ids, t)
            await rec(db, case, [ms(), ms("detected", "2099-10-22T16:00:00+00:00"), art("1.2.3.4")])
        async with client() as cl:
            lst = (await cl.get(f"/api/v1/cases/{case.id}/war-room/suggestions", headers=h)).json()
            cur = {x["payload"].get("key"): x["current"] for x in lst}
            assert cur[None] is None and cur["detected"] is None   # computed defaults are not "current"
            assert datetime.fromisoformat(cur["contained"]) == datetime(2099, 10, 22, 17, 0, tzinfo=UTC)
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
            out = await ws.record_candidates(db, case, [art("1.2.3.4")], channel="C1", source_ts="1.1", permalink="http://p",
                                             author={"slack_user": "U1", "author": "Ana Ruiz"})
            (s,) = out
            s.reply_ts = "5.5"
            await db.commit()
            r = await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=True, user=u)
            await db.commit()
            assert r.status == "accepted" and r.resolved_by_user_id == u.id and r.resolved_at is not None
            assert await _artifact_count(db, case.id) == 1
            a = (await db.execute(select(Artifact).join(CaseArtifact).where(CaseArtifact.case_id == case.id))).scalar_one()
            # provenance: who said it in Slack and where, on the new artifact and on the one activity entry
            source = "From Slack war room, posted by Ana Ruiz (U1): http://p"
            assert a.value == "1.2.3.4" and a.description == source
            (entry,) = (await db.execute(select(TimelineEvent).where(TimelineEvent.case_id == case.id))).scalars().all()
            assert entry.event_type == "artifact_added" and entry.content == f"Added artifact: 1.2.3.4 (ip). {source}"
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
            audit = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == t.id, AuditLog.action == "milestones_changed"))).scalars().one()
            assert audit.changes["source"] == "From Slack war room: http://p"
        assert [d[1] for d in delays] == ["containment.confirmed"]
    run(body)


def test_accept_milestone_inversion_stays_pending_with_reason(delays):
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(
                db, ids, t, detected_at=datetime(2099, 12, 1, tzinfo=UTC))).id)
            u = await make_user(db, ids)
            (s,) = await rec(db, case, [ms()])
            r = await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=True, user=u)
            await db.commit()
            assert r.status == "pending" and r.resolved_at is None
            assert r.payload["conflict"] == "Detected must not be after Contained"
            await db.refresh(case)
            assert case.contained_at is None
            assert (await db.execute(select(func.count()).select_from(AuditLog).where(
                AuditLog.tenant_id == t.id, AuditLog.action == "war_room_suggestion_conflict"))).scalar() == 1
            assert delays == []
            # the analyst fixes the other milestone: the same suggestion can now be accepted
            case.detected_at = datetime(2099, 10, 1, tzinfo=UTC)
            await db.commit()
            r = await ws.resolve_suggestion(db, tenant_id=t.id, suggestion_id=s.id, accept=True, user=u)
            await db.commit()
            assert r.status == "accepted" and "conflict" not in r.payload
            assert case.contained_at == datetime(2099, 10, 22, 18, 40, tzinfo=UTC)
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


def test_stale_loaded_row_does_not_double_apply(delays):
    """A session already holding the row as pending must re-read status after another session resolved it."""
    async def body(ids):
        async with AsyncSessionLocal() as db, AsyncSessionLocal() as other:
            t = await make_tenant(db, ids)
            case = await db.get(Case, (await make_case(db, ids, t)).id)
            u = await make_user(db, ids)
            (s,) = await rec(db, case, [art("1.2.3.4")])
            sid, uid, tid, cid = s.id, u.id, t.id, case.id
            stale = (await db.execute(select(WarRoomSuggestion).where(WarRoomSuggestion.id == sid))).scalars().one()
            assert stale.status == "pending"
            ou = await other.get(type(u), uid)
            await ws.resolve_suggestion(other, tenant_id=tid, suggestion_id=sid, accept=True, user=ou)
            await other.commit()
            again = await ws.resolve_suggestion(db, tenant_id=tid, suggestion_id=sid, accept=True, user=u)
            await db.commit()
            assert again.status == "accepted" and await _artifact_count(db, cid) == 1
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
            assert set(lst.json()[0]) == {"id", "kind", "payload", "status", "permalink", "created_at", "current"}
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
