"""SOC manager dashboard (GET /stats/team-insights) and the per-analyst additions to /stats/analyst-workload."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import delete

import app.db.base  # noqa: F401  (register all models)
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseStatus, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.escalation import CaseEscalation
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower, Notification
from app.models.tenant import Tenant
from app.models.user import User
from app.services.case_service import apply_case_update

URL = "/api/v1/stats/team-insights"


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


def _ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


def _week():
    """Explicit 7-day range ending today (UTC); the previous period is the 7 days before it."""
    today = datetime.now(timezone.utc).date()
    return {"start": (today - timedelta(days=6)).isoformat(), "end": today.isoformat()}


async def _scenario(monkeypatch, body):
    """Two tenants; T has admin + analysts a, b, c + a viewer. body(client, state, ctx) adds cases via ctx.case()."""
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    state = {"role": "admin", "tenant": None}
    monkeypatch.setattr(deps, "_active_role", lambda u: state["role"])
    try:
        async with AsyncSessionLocal() as db:
            t, t2 = Tenant(name="ti", slug=f"ti-{h}"), Tenant(name="ti2", slug=f"ti2-{h}")
            users = [User(email=f"ti-{h}-{n}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                          is_active=True, full_name=n.upper()) for n in ("admin", "a", "b", "c", "viewer")]
            db.add_all([t, t2, *users])
            await db.flush()
            ids["tenants"] += [t.id, t2.id]
            ids["users"] += [u.id for u in users]
            roles = ("admin", "analyst", "analyst", "analyst", "viewer")
            db.add_all([TenantMembership(user_id=u.id, tenant_id=t.id, role=r) for u, r in zip(users, roles)])
            db.add(TenantMembership(user_id=users[1].id, tenant_id=t2.id, role="analyst"))
            await db.commit()
            u = {n: x.id for n, x in zip(("admin", "a", "b", "c", "viewer"), users)}

        class Ctx:
            T, T2 = ids["tenants"]
            U = u

            @staticmethod
            async def case(tenant=None, **kw):
                kw.setdefault("title", "c")
                kw.setdefault("status", CaseStatus.OPEN)
                kw.setdefault("severity", "low")
                async with AsyncSessionLocal() as db:
                    c = Case(tenant_id=tenant or ids["tenants"][0], **kw)
                    db.add(c)
                    await db.commit()
                    ids["cases"].append(c.id)
                    return c.id

            @staticmethod
            async def add(*objs):
                async with AsyncSessionLocal() as db:
                    db.add_all(objs)
                    await db.commit()

            @staticmethod
            async def update(case_id, data, user_id):
                async with AsyncSessionLocal() as db:
                    case = await db.get(Case, case_id)
                    await apply_case_update(db, case=case, update_data=data, user_id=user_id)
                    await db.commit()

        state["tenant"] = ids["tenants"][0]
        app.dependency_overrides[deps.get_current_active_user] = lambda: None
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
        app.dependency_overrides[deps.require_admin] = lambda: None if state["role"] == "admin" else _deny()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            await body(client, state, Ctx)
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as d:
            tids, cids = ids["tenants"], ids["cases"]
            if tids:
                await d.execute(delete(Notification).where(Notification.tenant_id.in_(tids)))
                await d.execute(delete(CaseFollower).where(CaseFollower.tenant_id.in_(tids)))
                await d.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
                await d.execute(delete(CaseTriageResult).where(CaseTriageResult.tenant_id.in_(tids)))
                await d.execute(delete(CaseEscalation).where(CaseEscalation.tenant_id.in_(tids)))
            if cids:
                await d.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(cids)))
                await d.execute(delete(Case).where(Case.id.in_(cids)))
            if tids:
                await d.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_(tids)))
            if ids["users"]:
                await d.execute(delete(User).where(User.id.in_(ids["users"])))
            if tids:
                await d.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await d.commit()
        await engine.dispose()


def _closed(created, ack_min, res_min, **kw):
    return dict(status=CaseStatus.CLOSED, created_at=created, acknowledged_at=created + timedelta(minutes=ack_min),
                resolved_at=created + timedelta(minutes=res_min), **kw)


def test_kpis_medians_previous_period_and_isolation(monkeypatch):
    async def body(client, state, ctx):
        # Current period: 3 created (2 closed), MTTA median(10, 30, 20) = 20, MTTR median(60, 180) = 120.
        await ctx.case(**_closed(_ago(days=2), 10, 60))
        await ctx.case(**_closed(_ago(days=3), 30, 180))
        await ctx.case(owner_id=ctx.U["a"], severity="info", created_at=_ago(days=1),
                       acknowledged_at=_ago(days=1) + timedelta(minutes=20))
        # Previous period: 1 created and closed.
        await ctx.case(**_closed(_ago(days=10), 40, 100))
        # Open since before both periods, unassigned, one breach stamped in each period.
        await ctx.case(severity="info", created_at=_ago(days=20), sla_response_breach_notified_at=_ago(days=1),
                       sla_resolution_breach_notified_at=_ago(days=9))
        # Other tenant: must not count anywhere.
        await ctx.case(tenant=ctx.T2, **_closed(_ago(days=2), 1, 2))
        await ctx.case(tenant=ctx.T2, severity="critical", created_at=_ago(days=2))

        r = await client.get(URL, params=_week())
        assert r.status_code == 200, r.text
        d = r.json()
        k = d["kpis"]
        assert k["created"] == {"value": 3, "previous": 1}
        assert k["closed"] == {"value": 2, "previous": 1}
        assert k["mtta_minutes"] == {"value": 20.0, "previous": 40.0}
        assert k["mttr_minutes"] == {"value": 120.0, "previous": 100.0}
        assert k["sla_breaches"] == {"value": 1, "previous": 1}
        assert k["sla_breaches_split"] == {"response": 1, "resolution": 0}
        assert k["open_backlog"] == {"value": 2, "previous": 1}
        assert k["unassigned_open"]["value"] == 1
        assert len(d["trend"]) == 7
        assert sum(x["created"] for x in d["trend"]) == 3 and sum(x["closed"] for x in d["trend"]) == 2
        today = datetime.now(timezone.utc).date()
        assert d["previous_start"] == (today - timedelta(days=13)).isoformat()
        assert d["previous_end"] == (today - timedelta(days=7)).isoformat()
        bba = {x["name"]: x for x in d["backlog_by_analyst"]}
        assert bba["Unassigned"]["total"] == 1 and bba["A"]["open"] == 1 and set(bba) == {"Unassigned", "A"}
        assert all(i["id"] != "unassigned_urgent" for i in d["insights"])  # T2's critical case does not leak
        growing = next(i for i in d["insights"] if i["id"] == "backlog_growing")  # 3 created > 2 closed
        assert "(+1)" in growing["detail"] and growing["cases"] == []

        # Other tenant sees only its own cases.
        state["tenant"] = ctx.T2
        k2 = (await client.get(URL, params=_week())).json()["kpis"]
        assert k2["created"]["value"] == 2 and k2["closed"]["value"] == 1 and k2["open_backlog"]["value"] == 1

    asyncio.run(_scenario(monkeypatch, body))


def test_empty_tenant_has_no_nan_and_all_clear(monkeypatch):
    async def body(client, state, ctx):
        r = await client.get(URL)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["kpis"]["mtta_minutes"] == {"value": None, "previous": None}
        assert d["kpis"]["open_backlog"] == {"value": 0, "previous": 0}
        assert d["false_positive_rate"] is None and d["outcomes"] == [] and d["insights"] == []
        assert d["backlog_by_analyst"] == [] and all(b["total"] == 0 for b in d["aging"])

        # Benign data: nothing fires.
        await ctx.case(owner_id=ctx.U["a"], severity="info", created_at=_ago(days=10), updated_at=_ago(days=1))
        await ctx.case(**_closed(_ago(hours=5), 5, 60, outcome="true_positive_with_impact", source="edr"))
        r = await client.get(URL, params=_week())
        assert r.json()["insights"] == []
        assert r.json()["false_positive_rate"] == 0.0

    asyncio.run(_scenario(monkeypatch, body))


def test_aging_buckets_by_severity(monkeypatch):
    async def body(client, state, ctx):
        for sev, age in (("critical", {"hours": 2}), ("high", {"days": 2}), ("medium", {"days": 5}),
                         ("low", {"days": 10}), ("info", {"days": 40}), ("critical", {"days": 45})):
            await ctx.case(severity=sev, owner_id=ctx.U["b"], created_at=_ago(**age), updated_at=_ago(minutes=1))
        await ctx.case(**_closed(_ago(days=50), 1, 2))  # closed: not in aging
        aging = {b["bucket"]: b for b in (await client.get(URL)).json()["aging"]}
        assert list(aging) == ["<1d", "1-3d", "3-7d", "7-30d", ">30d"]
        assert aging["<1d"]["critical"] == 1 and aging["<1d"]["total"] == 1
        assert aging["1-3d"]["high"] == 1 and aging["3-7d"]["medium"] == 1 and aging["7-30d"]["low"] == 1
        assert aging[">30d"] == {"bucket": ">30d", "critical": 1, "high": 0, "medium": 0, "low": 0, "info": 1,
                                 "total": 2}

    asyncio.run(_scenario(monkeypatch, body))


def test_insight_rules_fire(monkeypatch):
    async def body(client, state, ctx):
        U = ctx.U
        urgent = await ctx.case(severity="critical", created_at=_ago(hours=2))          # unassigned + past SLA
        young = await ctx.case(severity="high", created_at=_ago(minutes=30))            # unassigned, < 1h: no
        a_cases = [await ctx.case(owner_id=U["a"], created_at=_ago(minutes=10)) for _ in range(5)]
        await ctx.case(owner_id=U["b"], created_at=_ago(minutes=10))
        stale = await ctx.case(owner_id=U["b"], severity="info", created_at=_ago(days=10), updated_at=_ago(days=8))
        busy = await ctx.case(owner_id=U["c"], severity="info", created_at=_ago(days=10))
        await ctx.add(TimelineEvent(case_id=busy, event_type="comment", content="x", created_at=_ago(days=1)))
        snapshot = {"tiers": []}
        esc = dict(tenant_id=ctx.T, reason="r", summary="s", requested_help="h", status="pending",
                   policy_snapshot=snapshot)
        await ctx.add(CaseEscalation(case_id=young, due_at=_ago(minutes=5), **esc),
                      CaseEscalation(case_id=a_cases[0], due_at=_ago(minutes=-30), **esc))
        # Closed in the period (created 20 days ago, resolved an hour ago): noisy source 3/5 FP fires, good source
        # 1/5 does not, tiny source 2/2 is below the minimum. "unknown" outcomes are not classified.
        long = 20 * 1440 - 60
        for src, outcomes in (("noisy-edr", ["false_positive"] * 3 + ["true_positive_without_impact"] * 2 + ["unknown"]),
                              ("good-siem", ["false_positive"] + ["legitimate"] * 4),
                              ("tiny", ["false_positive"] * 2)):
            for o in outcomes:
                await ctx.case(**_closed(_ago(days=20), 5, long, source=src, outcome=o))
        # Other tenant overdue escalation / unassigned critical: must not show up.
        other = await ctx.case(tenant=ctx.T2, severity="critical", created_at=_ago(hours=3))
        await ctx.add(CaseEscalation(case_id=other, due_at=_ago(hours=1), **{**esc, "tenant_id": ctx.T2}))

        d = (await client.get(URL, params=_week())).json()
        ins = {i["id"]: i for i in d["insights"]}
        ids = lambda key: {c["id"] for c in ins[key]["cases"]}  # noqa: E731

        assert ids("unassigned_urgent") == {urgent}
        assert urgent in ids("past_sla") and young not in ids("past_sla")
        assert ids("escalations_overdue") == {young}
        assert f"overloaded:{U['a']}" in ins and ids(f"overloaded:{U['a']}") == set(a_cases)
        assert not any(k.startswith("overloaded:") and k != f"overloaded:{U['a']}" for k in ins)
        assert ids("stale") == {stale}
        assert "backlog_growing" not in ins  # 8 created vs 16 closed
        assert "fp_source:noisy-edr" in ins and "fp_source:good-siem" not in ins and "fp_source:tiny" not in ins
        assert ins["fp_source:noisy-edr"]["count"] == 3
        assert ins["unassigned_urgent"]["level"] == "critical"
        assert d["kpis"]["unassigned_open"]["value"] == 2
        # Outcome mix: 6 FP of 12 classified ("unknown" excluded).
        assert {o["outcome"]: o["count"] for o in d["outcomes"]}["false_positive"] == 6
        assert d["closed_classified"] == 12 and d["false_positive_rate"] == 0.5

    asyncio.run(_scenario(monkeypatch, body))


def test_workload_median_resolve_and_sla_breaches(monkeypatch):
    async def body(client, state, ctx):
        U = ctx.U
        c1 = await ctx.case(owner_id=U["a"], created_at=_ago(hours=2))
        c2 = await ctx.case(owner_id=U["a"], created_at=_ago(hours=4))
        await ctx.update(c1, {"status": CaseStatus.RESOLVED}, U["a"])
        await ctx.update(c2, {"status": CaseStatus.CLOSED, "outcome": "legitimate", "closing_note": "expected activity"}, U["a"])
        breached = await ctx.case(owner_id=U["b"], sla_resolution_breach_notified_at=_ago(hours=1))
        await ctx.case(owner_id=U["b"], sla_response_breach_notified_at=_ago(days=40))  # outside the period
        await ctx.case(tenant=ctx.T2, owner_id=U["a"], sla_response_breach_notified_at=_ago(hours=1))

        r = await client.get("/api/v1/stats/analyst-workload", params=_week())
        rows = {x["user_id"]: x for x in r.json()["analysts"]}
        assert 175 < rows[U["a"]]["median_resolve_minutes"] < 185  # median(120, 240) = 180
        assert rows[U["b"]]["median_resolve_minutes"] is None
        assert rows[U["a"]]["sla_breached"] == 0 and rows[U["b"]]["sla_breached"] == 1
        r = await client.get("/api/v1/stats/analyst-workload/cases",
                             params={"user_id": U["b"], "metric": "sla_breached", **_week()})
        assert {x["id"] for x in r.json()["cases"]} == {breached}

    asyncio.run(_scenario(monkeypatch, body))


def test_team_insights_requires_admin(monkeypatch):
    async def body(client, state, ctx):
        for role in ("analyst", "viewer"):
            state["role"] = role
            assert (await client.get(URL)).status_code == 403

    asyncio.run(_scenario(monkeypatch, body))
