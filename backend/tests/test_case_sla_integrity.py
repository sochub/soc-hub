"""Case/SLA data integrity: the response clock stops for cases created past NEW (and the backfill for old rows),
the SLA sweep stamps + commits before anything is sent and emails each person once, explicit nulls are refused on
case updates, cases are only assigned to active analysts/admins, updates lock the row, stats periods are bounded,
and the artifacts list has a stable order."""
import asyncio
import importlib.util
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError

import app.notifications.delivery as delivery
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseSeverity, CaseStatus, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower, Notification
from app.models.tenant import Tenant
from app.models.user import User
from app.services import case_service
from app.tasks import sla as sla_task
from app.utils.sla import compute_sla_state

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "b8d0f2a4c6e1_backfill_case_sla_stamps.py"


def _as(user_id: int, tenant_id: int, role: str):
    return SimpleNamespace(id=user_id, is_super_admin=False, is_active=True, _active_tenant_id=tenant_id,
                           memberships=[SimpleNamespace(tenant_id=tenant_id, role=role)])


async def _setup():
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t = Tenant(name="sla-int", slug=f"sla-int-{h}")
        people = {n: User(email=f"sli-{n}-{h}@example.test", full_name=n.title(), is_active=(n != "dead"),
                          hashed_password=get_password_hash(secrets.token_hex(8)))
                  for n in ("admin", "admin2", "analyst", "viewer", "dead")}
        db.add_all([t, *people.values()])
        await db.flush()
        roles = {"admin": "admin", "admin2": "admin", "analyst": "analyst", "viewer": "viewer", "dead": "analyst"}
        db.add_all([TenantMembership(user_id=p.id, tenant_id=t.id, role=roles[n]) for n, p in people.items()])
        await db.commit()
        return t.id, {n: p.id for n, p in people.items()}


async def _cleanup(tid, uids):
    async with AsyncSessionLocal() as db:
        case_ids = select(Case.id).where(Case.tenant_id == tid)
        for stmt in (delete(Notification).where(Notification.tenant_id == tid),
                     delete(CaseFollower).where(CaseFollower.tenant_id == tid),
                     delete(AuditLog).where(AuditLog.tenant_id == tid),
                     delete(TimelineEvent).where(TimelineEvent.case_id.in_(case_ids)),
                     delete(CaseTriageResult).where(CaseTriageResult.tenant_id == tid),
                     delete(Artifact).where(Artifact.tenant_id == tid),
                     delete(Case).where(Case.tenant_id == tid),
                     delete(TenantMembership).where(TenantMembership.tenant_id == tid),
                     delete(Tenant).where(Tenant.id == tid),
                     delete(User).where(User.id.in_(uids))):
            await db.execute(stmt)
        await db.commit()
    await engine.dispose()


def run(body):
    async def main():
        await engine.dispose()
        tid, u = await _setup()
        try:
            await body(tid, u)
        finally:
            app.dependency_overrides.clear()
            await _cleanup(tid, list(u.values()))
    asyncio.run(main())


def _client(user):
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: user._active_tenant_id
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


# ── 1. response SLA stops for cases created past NEW ────────────────────────────────────────

def test_create_past_new_stamps_acknowledged_and_resolved():
    async def body(tid, u):
        async with AsyncSessionLocal() as db:
            out = {}
            for st in (CaseStatus.NEW, CaseStatus.OPEN, CaseStatus.RESOLVED):
                case, _ = await case_service.create_case_record(
                    db, tenant_id=tid, data={"title": st.value, "status": st, "severity": CaseSeverity.CRITICAL},
                    user_id=u["analyst"])
                out[st] = case
            await db.commit()
            assert out[CaseStatus.NEW].acknowledged_at is None and out[CaseStatus.NEW].resolved_at is None
            assert out[CaseStatus.OPEN].acknowledged_at is not None and out[CaseStatus.OPEN].resolved_at is None
            assert out[CaseStatus.RESOLVED].acknowledged_at is not None
            assert out[CaseStatus.RESOLVED].resolved_at is not None
            for st in (CaseStatus.OPEN, CaseStatus.RESOLVED):
                await db.refresh(out[st])
                assert compute_sla_state(out[st], {})["response_status"] == "met"
    run(body)


def test_status_change_while_unacknowledged_stops_response_clock():
    async def body(tid, u):
        async with AsyncSessionLocal() as db:
            # A case created OPEN before acknowledged_at was stamped on create.
            case = Case(title="legacy", tenant_id=tid, status=CaseStatus.OPEN, owner_id=u["analyst"])
            db.add(case)
            await db.commit()
            await case_service.apply_case_update(db, case=case, update_data={"status": CaseStatus.IN_PROGRESS},
                                                 user_id=u["analyst"])
            await db.commit()
            assert case.acknowledged_at is not None
            stamped = case.acknowledged_at
            await case_service.apply_case_update(db, case=case, update_data={"status": CaseStatus.NEW}, user_id=None)
            await case_service.apply_case_update(db, case=case, update_data={"status": CaseStatus.OPEN}, user_id=None)
            await db.commit()
            assert case.acknowledged_at == stamped, "set once"
    run(body)


def test_backfill_migration_fills_nulls_only(monkeypatch):
    async def body(tid, u):
        spec = importlib.util.spec_from_file_location("backfill_mig", MIGRATION)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        stmts = []
        monkeypatch.setattr(mig, "op", SimpleNamespace(execute=stmts.append))
        mig.upgrade()
        mig.downgrade()  # a no-op
        assert len(stmts) == 2

        created = datetime.now(timezone.utc) - timedelta(days=3)
        updated = created + timedelta(hours=5)
        kept = created + timedelta(minutes=7)
        async with AsyncSessionLocal() as db:
            rows = {
                "new": Case(title="n", tenant_id=tid, status=CaseStatus.NEW, severity=CaseSeverity.CRITICAL),
                "open": Case(title="o", tenant_id=tid, status=CaseStatus.OPEN, severity=CaseSeverity.CRITICAL),
                "closed": Case(title="c", tenant_id=tid, status=CaseStatus.CLOSED, severity=CaseSeverity.CRITICAL),
                "has_ack": Case(title="a", tenant_id=tid, status=CaseStatus.OPEN, severity=CaseSeverity.CRITICAL,
                                acknowledged_at=kept),
            }
            db.add_all(rows.values())
            await db.flush()
            ids = {k: c.id for k, c in rows.items()}
            id_list = ",".join(str(i) for i in ids.values())
            await db.execute(text(f"UPDATE cases SET created_at = :c, updated_at = :u WHERE id IN ({id_list})"),
                             {"c": created, "u": updated})
            for s in stmts:  # scoped to this test's rows: never a whole-table write from a test
                await db.execute(text(f"{s} AND id IN ({id_list})"))
            await db.commit()
            got = {k: await db.get(Case, i, populate_existing=True) for k, i in ids.items()}
            assert got["new"].acknowledged_at is None and got["new"].resolved_at is None
            assert got["open"].acknowledged_at == created and got["open"].resolved_at is None
            assert got["closed"].acknowledged_at == created and got["closed"].resolved_at == updated
            assert got["has_ack"].acknowledged_at == kept
            # Acknowledged at creation: the response clock reads met, so the sweep has no response breach to send.
            assert compute_sla_state(got["open"], {})["response_status"] == "met"
    run(body)


# ── 2. SLA sweep: stamp + commit, then send; one path per person ──────────────────────────

def test_sla_sweep_commits_before_sending_and_emails_admins_once(monkeypatch):
    async def body(tid, u):
        monkeypatch.setattr(delivery, "_enqueue", lambda ids: None)
        async with AsyncSessionLocal() as db:
            case = Case(title="breach", tenant_id=tid, status=CaseStatus.NEW, severity=CaseSeverity.CRITICAL,
                        owner_id=u["admin"], created_at=datetime.now(timezone.utc) - timedelta(hours=2))
            db.add(case)
            await db.commit()
            cid = case.id

            admin2_email = (await db.get(User, u["admin2"])).email

        sent, committed = [], []
        real_claim = sla_task._claim

        async def claim(db, case_id, overrides):
            out = await real_claim(db, case_id, overrides)
            if case_id == cid:  # what is returned gets sent; by then another session already sees the stamp
                async with AsyncSessionLocal() as other:
                    committed.append((await other.get(Case, cid)).sla_response_breach_notified_at)
            return out
        monkeypatch.setattr(sla_task, "_claim", claim)

        def fake_email_delay(case_id, title, severity, kind, emails):
            if case_id == cid:
                sent.append((kind, sorted(emails)))

        slack = []
        from app.tasks import slack as slack_tasks
        monkeypatch.setattr(sla_task.send_sla_breach_emails_task, "delay", fake_email_delay)
        monkeypatch.setattr(slack_tasks.slack_case_event_task, "delay",
                            lambda tenant_id, kind, case_id, extra: slack.append(case_id) if case_id == cid else None)

        await sla_task._run()
        # Only response breached (2h > 15 min; resolution target 4h). The owner (an admin) is reached by the
        # notification path, so the direct email goes to the other admin only.
        assert sent == [("response", [admin2_email])]
        assert committed and committed[0] is not None
        assert slack == [cid]
        async with AsyncSessionLocal() as db:
            notes = (await db.execute(select(Notification.user_id).where(
                Notification.case_id == cid, Notification.type == "sla_breach"))).scalars().all()
            assert notes == [u["admin"]]

        await sla_task._run()  # already stamped: nothing again
        assert len(sent) == 1 and slack == [cid]
    run(body)


def test_sla_sweep_does_not_swallow_soft_time_limit(monkeypatch):
    async def body(tid, u):
        async def boom(db, tenant_id):
            raise SoftTimeLimitExceeded()
        monkeypatch.setattr(sla_task, "_check_tenant", boom)
        with pytest.raises(SoftTimeLimitExceeded):
            await sla_task._run()
    run(body)


def test_digest_claim_released_when_nothing_sent(monkeypatch):
    from app.services import email_service
    from app.tasks import portfolio as portfolio_task

    async def body(tid, u):
        async with AsyncSessionLocal() as db:
            t = await db.get(Tenant, tid)
            t.digest_recipients = ["ciso@example.test"]
            await db.commit()
        monkeypatch.setattr(email_service, "_smtp_configured", lambda: True)
        monkeypatch.setattr(email_service, "_send_email", lambda *a, **k: False)  # SMTP down
        await engine.dispose()
        await asyncio.to_thread(portfolio_task.send_client_digests_task.run)
        async with AsyncSessionLocal() as db:
            assert (await db.get(Tenant, tid)).digest_last_sent_at is None, "retried next hour"
        sent = []
        monkeypatch.setattr(email_service, "_send_email", lambda to, *a, **k: sent.append(to) or True)
        await engine.dispose()
        await asyncio.to_thread(portfolio_task.send_client_digests_task.run)
        async with AsyncSessionLocal() as db:
            assert (await db.get(Tenant, tid)).digest_last_sent_at is not None
        assert sent.count("ciso@example.test") == 1
    run(body)


# ── 3/4. PUT /cases: explicit nulls, assignable owners ─────────────────────────────────────

def test_update_rejects_nulls_and_unassignable_owners():
    async def body(tid, u):
        async with AsyncSessionLocal() as db:
            case = Case(title="put", tenant_id=tid, status=CaseStatus.OPEN, owner_id=u["analyst"])
            db.add(case)
            await db.commit()
            cid = case.id
        async with _client(_as(u["analyst"], tid, "analyst")) as c:
            for field in ("status", "severity", "title", "source"):
                r = await c.put(f"/api/v1/cases/{cid}", json={field: None})
                assert r.status_code == 422 and field in r.text, (field, r.text)
            for bad in (u["viewer"], u["dead"], 999999999):
                r = await c.put(f"/api/v1/cases/{cid}", json={"owner_id": bad})
                assert r.status_code == 422, (bad, r.text)
            r = await c.put(f"/api/v1/cases/{cid}", json={"owner_id": u["admin"]})
            assert r.status_code == 200 and r.json()["owner_id"] == u["admin"], r.text
            r = await c.put(f"/api/v1/cases/{cid}", json={"owner_id": None})  # null still unassigns
            assert r.status_code == 200 and r.json()["owner_id"] is None, r.text
        async with AsyncSessionLocal() as db:
            c = await db.get(Case, cid)
            assert c.status == CaseStatus.OPEN and c.severity is not None and c.title == "put" and c.source
            # Same rule on the shared service path (Slack/workflow callers)
            with pytest.raises(Exception) as e:
                await case_service.apply_case_update(db, case=c, update_data={"severity": None}, user_id=None)
            assert getattr(e.value, "status_code", None) == 422
            await db.rollback()
    run(body)


# ── 6. updates lock the row ────────────────────────────────────────────────────────────────

def test_apply_case_update_takes_row_lock():
    async def body(tid, u):
        async with AsyncSessionLocal() as db:
            case = Case(title="lock", tenant_id=tid, status=CaseStatus.OPEN)
            db.add(case)
            await db.commit()
            cid = case.id
        async with AsyncSessionLocal() as a, AsyncSessionLocal() as b:
            ca = await a.get(Case, cid)
            await case_service.apply_case_update(a, case=ca, update_data={"severity": CaseSeverity.HIGH}, user_id=None)
            with pytest.raises(DBAPIError):
                await b.execute(select(Case).where(Case.id == cid).with_for_update(nowait=True))
            await b.rollback()
            await a.rollback()
    run(body)


# ── 7. stats period bounds ─────────────────────────────────────────────────────────────────

def test_stats_period_is_bounded():
    async def body(tid, u):
        async with _client(_as(u["admin"], tid, "admin")) as c:
            for path in ("/api/v1/stats/analyst-workload", "/api/v1/stats/team-insights"):
                r = await c.get(path, params={"start": "2024-01-01", "end": "2025-06-01"})
                assert r.status_code == 422 and "366" in r.text, r.text
                r = await c.get(path, params={"start": "9999-12-30", "end": "9999-12-31"})
                assert r.status_code == 422, r.text
                r = await c.get(path, params={"start": "0001-01-01", "end": "0001-01-02"})
                assert r.status_code == 422, r.text
                r = await c.get(path, params={"start": "2024-01-01", "end": "2024-12-31"})  # 366 days: ok
                assert r.status_code == 200, r.text
    run(body)


# ── 8. artifacts list order ────────────────────────────────────────────────────────────────

def test_artifacts_list_newest_first():
    async def body(tid, u):
        async with AsyncSessionLocal() as db:
            arts = [Artifact(tenant_id=tid, artifact_type=ArtifactType.IP, value=f"198.51.100.{i}") for i in range(3)]
            db.add_all(arts)
            await db.commit()
        async with _client(_as(u["analyst"], tid, "analyst")) as c:
            r = await c.get("/api/v1/artifacts/")
            ids = [a["id"] for a in r.json()]
            assert r.status_code == 200 and ids == sorted(ids, reverse=True) and len(ids) == 3, r.text
    run(body)
