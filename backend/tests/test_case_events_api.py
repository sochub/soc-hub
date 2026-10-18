"""Investigation timeline API: ordering by event_date, artifact linking rules, tenant isolation, report lane."""
import asyncio
import secrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.case_event import CaseEvent
from app.models.tenant import Tenant
from app.models.user import User


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": [], "artifacts": []}
    state = {"tenant": None, "role": "analyst", "user": None}
    monkeypatch.setattr(deps, "_active_role", lambda u: state["role"])
    async with AsyncSessionLocal() as db:
        try:
            t, t2 = Tenant(name="ev-test", slug=f"ev-{h}-a"), Tenant(name="ev-test", slug=f"ev-{h}-b")
            user = User(email=f"ev-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            ca, cb = Case(title="ev-a", tenant_id=t.id), Case(title="ev-b", tenant_id=t2.id)
            db.add_all([ca, cb])
            await db.flush()
            ids["cases"] += [int(ca.id), int(cb.id)]
            mine = Artifact(tenant_id=t.id, value=f"10.0.0.{h[:2]}", artifact_type="ip")
            other = Artifact(tenant_id=t2.id, value=f"10.9.9.{h[:2]}", artifact_type="ip")
            db.add_all([mine, other])
            await db.flush()
            ids["artifacts"] += [int(mine.id), int(other.id)]
            db.add(CaseArtifact(case_id=ca.id, artifact_id=mine.id))
            await db.commit()
            tid, tid2 = ids["tenants"]
            cid, cid2 = ids["cases"]
            aid, aid_other = ids["artifacts"]
            state["tenant"], state["user"] = tid, user
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())
            base = f"/api/v1/cases/{cid}/events"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                # a) create two events out of order; list comes back by event_date
                r = await c.post(base, json={"event_date": "2026-10-02T10:00:00Z", "title": "Lateral move",
                                             "category": "lateral_movement", "artifact_ids": [aid],
                                             "tags": ["psexec"]})
                assert r.status_code == 201, r.text
                late = r.json()
                assert late["artifacts"][0]["id"] == aid and late["artifacts"][0]["type_key"] == "ip", late
                r = await c.post(base, json={"event_date": "2026-10-01T09:00:00Z", "title": "Phish opened",
                                             "category": "initial_access", "source": "Proxy"})
                assert r.status_code == 201, r.text
                early = r.json()
                r = await c.get(base)
                assert [e["id"] for e in r.json()] == [early["id"], late["id"]], r.json()
                # b) bad category and foreign artifact are rejected
                r = await c.post(base, json={"event_date": "2026-10-01T09:00:00Z", "title": "x", "category": "nope"})
                assert r.status_code == 422, r.text
                r = await c.post(base, json={"event_date": "2026-10-01T09:00:00Z", "title": "x",
                                             "artifact_ids": [aid_other]})
                assert r.status_code == 400, r.text
                # c) update flags + unlink artifacts
                r = await c.put(f"{base}/{late['id']}", json={"flagged": True, "in_summary": False, "artifact_ids": []})
                assert r.status_code == 200 and r.json()["flagged"] is True and r.json()["artifacts"] == [], r.text
                # d) viewer can read but not write
                state["role"] = "viewer"
                assert (await c.get(base)).status_code == 200
                assert (await c.post(base, json={"event_date": "2026-10-01T09:00:00Z", "title": "x"})).status_code == 403
                state["role"] = "analyst"
                # e) other tenant sees 404 for the case and its events
                state["tenant"] = tid2
                assert (await c.get(base)).status_code == 404
                assert (await c.delete(f"{base}/{early['id']}")).status_code == 404
                state["tenant"] = tid
                # f) only in_summary events reach the report timeline, on the investigation lane
                from app.reports import builder
                async with AsyncSessionLocal() as db2:
                    case_row = (await db2.get(Case, cid))
                    rep = await builder.build_report(db2, case_row, full=False, generated_by=user)
                inv = [e for e in rep["timeline"] if e["lane"] == "investigation"]
                assert [e["text"] for e in inv] == ["Phish opened"] and inv[0]["actor"] == "Proxy", inv
                # g) delete
                assert (await c.delete(f"{base}/{early['id']}")).status_code == 204
                assert (await c.get(base)).json() == [] or all(e["id"] != early["id"] for e in (await c.get(base)).json())
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db3:
                await db3.execute(delete(CaseEvent).where(CaseEvent.case_id.in_(ids["cases"])))
                await db3.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(ids["cases"])))
                await db3.execute(delete(Artifact).where(Artifact.id.in_(ids["artifacts"])))
                await db3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await db3.execute(delete(User).where(User.id.in_(ids["users"])))
                await db3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await db3.commit()
            await engine.dispose()


def test_case_events_api(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
