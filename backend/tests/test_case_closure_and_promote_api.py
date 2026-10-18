"""Case outcome required to close, and artifact -> IOC promote (dedupe, tenant isolation, roles)."""
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
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
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
            t, t2 = Tenant(name="cl-test", slug=f"cl-{h}-a"), Tenant(name="cl-test", slug=f"cl-{h}-b")
            user = User(email=f"cl-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            ca = Case(title="cl-a", tenant_id=t.id)
            db.add(ca)
            await db.flush()
            ids["cases"].append(int(ca.id))
            mine = Artifact(tenant_id=t.id, value=f"10.0.0.{h[:2]}", artifact_type="ip")
            other = Artifact(tenant_id=t2.id, value=f"10.9.9.{h[:2]}", artifact_type="ip")
            db.add_all([mine, other])
            await db.flush()
            ids["artifacts"] += [int(mine.id), int(other.id)]
            db.add(CaseArtifact(case_id=ca.id, artifact_id=mine.id))
            await db.commit()
            tid = ids["tenants"][0]
            cid = ids["cases"][0]
            aid, aid_other = ids["artifacts"]
            val = mine.value
            state["tenant"], state["user"] = tid, user
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                url = f"/api/v1/cases/{cid}"
                # closing needs an outcome (kanban drag sends only status)
                assert (await c.put(url, json={"status": "closed"})).status_code == 422
                r = await c.put(url, json={"status": "closed", "outcome": "false_positive",
                                           "closing_note": "benign scanner"})
                assert r.status_code == 200, r.text
                assert r.json()["outcome"] == "false_positive" and r.json()["closing_note"] == "benign scanner"
                assert (await c.put(url, json={"outcome": "nope"})).status_code == 422
                # promote: create, dedupe, tenant isolation, role
                p = f"/api/v1/artifacts/{aid}/promote"
                r = await c.post(p, json={"case_id": cid})
                assert r.status_code == 201, r.text
                assert r.json()["created"] is True and r.json()["ioc"]["ioc_type"] == "ip_address"
                assert r.json()["ioc"]["value"] == val
                ids["ioc"] = r.json()["ioc"]["id"]
                r = await c.post(p, json={"case_id": cid})
                assert r.status_code == 200 and r.json()["created"] is False, r.text
                assert (await c.post(f"/api/v1/artifacts/{aid_other}/promote", json={})).status_code == 404
                state["role"] = "viewer"
                assert (await c.post(p, json={})).status_code == 403
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db3:
                await db3.execute(delete(IOC).where(IOC.id == ids["ioc"]))
                await db3.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids["cases"])))
                await db3.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(ids["cases"])))
                await db3.execute(delete(Artifact).where(Artifact.id.in_(ids["artifacts"])))
                await db3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await db3.execute(delete(User).where(User.id.in_(ids["users"])))
                await db3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await db3.commit()
            await engine.dispose()


def test_case_closure_and_promote_api(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
