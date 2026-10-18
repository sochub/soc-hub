"""Alert triage API: severity filter/sort, PATCH, similar alerts, tenant-scoped bulk actions."""
import asyncio
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact
from app.models.case import Alert, Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": [], "alerts": []}
    state = {"tenant": None, "role": "analyst", "user": None}
    monkeypatch.setattr(deps, "_active_role", lambda u: state["role"])
    async with AsyncSessionLocal() as db:
        try:
            t, t2 = Tenant(name="al-test", slug=f"al-{h}-a"), Tenant(name="al-test", slug=f"al-{h}-b")
            user = User(email=f"al-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            case = Case(title="al-case", tenant_id=t.id)
            db.add_all([case, TenantMembership(user_id=user.id, tenant_id=t.id, role="analyst")])
            await db.flush()
            ids["cases"].append(int(case.id))
            a1 = Alert(tenant_id=t.id, source="s", external_id=f"{h}-1", title="a1", severity="high",
                       payload={"src_ip": "10.1.2.3"}, status="pending")
            a2 = Alert(tenant_id=t.id, source="s", external_id=f"{h}-2", title="a2", severity="low",
                       payload={"src_ip": "10.1.2.3", "user": "bob"}, status="pending")
            a3 = Alert(tenant_id=t.id, source="s", external_id=f"{h}-3", title="a3", severity="critical",
                       payload={"src_ip": "192.168.77.9"}, status="pending")
            ax = Alert(tenant_id=t2.id, source="s", external_id=f"{h}-x", title="ax", severity="high",
                       payload={"src_ip": "10.1.2.3"}, status="pending")
            db.add_all([a1, a2, a3, ax])
            await db.flush()
            ids["alerts"] += [int(a.id) for a in (a1, a2, a3, ax)]
            await db.commit()
            id1, id2, id3, idx = ids["alerts"]
            cid = ids["cases"][0]
            state["tenant"], state["user"] = ids["tenants"][0], user
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                # a) list: sort by severity, filter, no implicit status filter
                r = await c.get("/api/v1/alerts/", params={"sort": "severity"})
                assert r.status_code == 200, r.text
                mine = [a for a in r.json() if a["id"] in ids["alerts"]]
                assert [a["id"] for a in mine] == [id3, id1, id2], mine
                r = await c.get("/api/v1/alerts/", params={"severity": "low"})
                assert [a["id"] for a in r.json()] == [id2], r.json()
                assert {a["id"] for a in (await c.get("/api/v1/alerts/")).json()} == {id1, id2, id3}
                # b) PATCH validates severity, persists owner/severity
                r = await c.patch(f"/api/v1/alerts/{id1}", json={"severity": "bogus"})
                assert r.status_code == 422, r.text
                r = await c.patch(f"/api/v1/alerts/{id1}", json={"owner_id": user.id, "severity": "low"})
                assert r.status_code == 200 and r.json()["severity"] == "low" and r.json()["owner_id"] == user.id, r.text
                assert (await c.patch(f"/api/v1/alerts/{idx}", json={"severity": "low"})).status_code == 404
                state["tenant"] = ids["tenants"][1]  # user is not a member of tenant 2
                assert (await c.patch(f"/api/v1/alerts/{idx}", json={"owner_id": user.id})).status_code == 400
                state["tenant"] = ids["tenants"][0]
                # c) similar: shares 10.1.2.3, not a3, not itself, not other tenant
                r = await c.get(f"/api/v1/alerts/{id1}/similar")
                assert r.status_code == 200 and [a["id"] for a in r.json()] == [id2], r.text
                # d) bulk dismiss skips the foreign id
                r = await c.post("/api/v1/alerts/bulk", json={"ids": [id1, idx], "action": "dismiss"})
                assert r.status_code == 200 and r.json() == {"updated": [id1], "skipped": [idx]}, r.text
                # e) bulk promote
                r = await c.post("/api/v1/alerts/bulk", json={"ids": [id2], "action": "promote", "case_id": cid})
                assert r.status_code == 200 and r.json()["updated"] == [id2], r.text
                # f) viewer cannot write
                state["role"] = "viewer"
                assert (await c.patch(f"/api/v1/alerts/{id3}", json={"severity": "low"})).status_code == 403
                assert (await c.post("/api/v1/alerts/bulk", json={"ids": [id3], "action": "dismiss"})).status_code == 403
                state["role"] = "analyst"
            async with AsyncSessionLocal() as db2:
                rows = {a.id: a for a in (await db2.execute(select(Alert).where(Alert.id.in_(ids["alerts"])))).scalars()}
                assert rows[idx].status == "pending" and rows[id1].status == "dismissed"
                assert rows[id2].status == "promoted" and rows[id2].case_id == cid
                vals = (await db2.execute(select(Artifact.value).join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
                                          .where(CaseArtifact.case_id == cid))).scalars().all()
                assert "10.1.2.3" in vals, vals
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db3:
                art_ids = (await db3.execute(select(CaseArtifact.artifact_id)
                                             .where(CaseArtifact.case_id.in_(ids["cases"])))).scalars().all()
                await db3.execute(delete(Alert).where(Alert.id.in_(ids["alerts"])))
                await db3.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids["cases"])))
                await db3.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(ids["cases"])))
                await db3.execute(delete(Artifact).where(Artifact.id.in_(art_ids)))
                await db3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await db3.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(ids["users"])))
                await db3.execute(delete(User).where(User.id.in_(ids["users"])))
                await db3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await db3.commit()
            await engine.dispose()


def test_alert_triage_api(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
