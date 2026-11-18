"""Server-side filters, sort and X-Total-Count on the case, IOC and artifact lists (no silent 100-row cap)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.db.session import AsyncSessionLocal
from app.models.artifact import Artifact
from app.models.case import CaseSeverity, CaseStatus
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.membership import TenantMembership
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run
from tests.session_fixtures import make_web_token

UTC = timezone.utc


async def _member(db, ids, tenant, role="analyst"):
    u = await make_user(db, ids)
    db.add(TenantMembership(user_id=u.id, tenant_id=tenant.id, role=role))
    await db.commit()
    return u, {"Authorization": f"Bearer {await make_web_token(db, u, tenant.id)}"}


def _ids(r):
    return [c["id"] for c in r.json()]


def test_case_list_filters_sort_total():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            t, other = await make_tenant(db, ids), await make_tenant(db, ids)
            me, h = await _member(db, ids, t)
            now = datetime.now(UTC)
            a = await make_case(db, ids, t, title="Phishing wave", severity=CaseSeverity.HIGH, status=CaseStatus.OPEN,
                                owner_id=me.id, tags=["phish", "email"])
            b = await make_case(db, ids, t, title="Ransomware 50%", severity=CaseSeverity.CRITICAL,
                                status=CaseStatus.NEW, tags=["ransom"],
                                created_at=now - timedelta(days=30))  # long past every SLA target
            c = await make_case(db, ids, t, title="Closed one", severity=CaseSeverity.LOW, status=CaseStatus.CLOSED,
                                outcome="false_positive", closing_note="fp")
            c2 = await make_case(db, ids, t, title="no description", severity=CaseSeverity.INFO,
                                 status=CaseStatus.RESOLVED)
            c2.description = None
            await db.commit()
            x = await make_case(db, ids, other, title="Phishing elsewhere")
        async with client() as cl:
            r = await cl.get("/api/v1/cases/", headers=h)
            assert r.status_code == 200, r.text
            assert set(_ids(r)) == {a.id, b.id, c.id, c2.id} and x.id not in _ids(r)
            assert r.headers["x-total-count"] == "4"
            assert all(row["timeline_events"] == [] for row in r.json())

            r = await cl.get("/api/v1/cases/", headers=h, params={"limit": 2, "sort": "severity"})
            assert _ids(r) == [b.id, a.id] and r.headers["x-total-count"] == "4"
            r = await cl.get("/api/v1/cases/", headers=h, params={"skip": 2, "limit": 2, "sort": "severity"})
            assert _ids(r) == [c.id, c2.id]

            r = await cl.get("/api/v1/cases/", headers=h, params=[("status", "new"), ("status", "open")])
            assert set(_ids(r)) == {a.id, b.id} and r.headers["x-total-count"] == "2"
            r = await cl.get("/api/v1/cases/", headers=h, params=[("severity", "critical"), ("severity", "low")])
            assert set(_ids(r)) == {b.id, c.id}
            assert _ids(await cl.get("/api/v1/cases/", headers=h, params={"owner": "me"})) == [a.id]
            r = await cl.get("/api/v1/cases/", headers=h, params={"owner": "unassigned"})
            assert set(_ids(r)) == {b.id, c.id, c2.id}
            assert _ids(await cl.get("/api/v1/cases/", headers=h, params={"tag": "phish"})) == [a.id]

            # q: title/description substring (wildcards are literal), or the id
            assert _ids(await cl.get("/api/v1/cases/", headers=h, params={"q": "phishing"})) == [a.id]
            assert _ids(await cl.get("/api/v1/cases/", headers=h, params={"q": "50%"})) == [b.id]
            assert _ids(await cl.get("/api/v1/cases/", headers=h, params={"q": "%"})) == [b.id]
            assert a.id in _ids(await cl.get("/api/v1/cases/", headers=h, params={"q": f"#{a.id}"}))
            assert _ids(await cl.get("/api/v1/cases/", headers=h, params={"q": "description"})) == [c2.id]

            r = await cl.get("/api/v1/cases/", headers=h, params={"sla": "breached"})
            assert b.id in _ids(r) and a.id not in _ids(r) and r.headers["x-total-count"] == str(len(r.json()))
            r = await cl.get("/api/v1/cases/", headers=h, params={"sla": "pressure", "status": "new"})
            assert _ids(r) == [b.id]

            assert (await cl.get("/api/v1/cases/", headers=h, params={"status": "bogus"})).status_code == 422
            assert (await cl.get("/api/v1/cases/", headers=h, params={"sort": "title"})).status_code == 422
            assert (await cl.get("/api/v1/cases/", headers=h, params={"owner": "x"})).status_code == 422
    run(body)


def test_ioc_and_artifact_lists_total_and_search():
    async def body(ids):
        ioc_ids = []
        try:
            async with AsyncSessionLocal() as db:
                t, other = await make_tenant(db, ids), await make_tenant(db, ids)
                _, h = await _member(db, ids, t)
                case = await make_case(db, ids, t)
                ocase = await make_case(db, ids, other)
                for tid, cid, val in ((t.id, case.id, "203.0.113.7"), (t.id, case.id, "evil.example"),
                                      (t.id, case.id, "198.51.100.1"), (other.id, ocase.id, "203.0.113.8")):
                    i = IOC(tenant_id=tid, case_id=cid, ioc_type="ip", value=val)
                    a = Artifact(tenant_id=tid, value=val)
                    db.add_all([i, a])
                    await db.flush()
                    ioc_ids.append(i.id)
                    db.add(CaseArtifact(case_id=cid, artifact_id=a.id))
                await db.commit()
            async with client() as cl:
                for path in ("/api/v1/iocs/", "/api/v1/artifacts/"):
                    r = await cl.get(path, headers=h, params={"limit": 2})
                    assert r.status_code == 200, r.text
                    assert len(r.json()) == 2 and r.headers["x-total-count"] == "3", (path, r.headers)
                    r = await cl.get(path, headers=h, params={"skip": 2, "limit": 2})
                    assert len(r.json()) == 1
                    r = await cl.get(path, headers=h, params={"q": "203.0.113"})
                    assert [x["value"] for x in r.json()] == ["203.0.113.7"] and r.headers["x-total-count"] == "1"
                    r = await cl.get(path, headers=h, params={"q": "_"})
                    assert r.json() == [] and r.headers["x-total-count"] == "0"
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(IOC).where(IOC.id.in_(ioc_ids or [-1])))
                await db.commit()
    run(body)
