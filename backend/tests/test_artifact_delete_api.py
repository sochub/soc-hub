"""Deleting an artifact (hard delete and unlink-from-case) works for analysts, including when a timeline event links it."""
import asyncio
import secrets
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_event import CaseEvent
from app.models.tenant import Tenant
from app.models.user import User


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": [], "artifacts": []}
    monkeypatch.setattr(deps, "_active_role", lambda u: "analyst")
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="ad-test", slug=f"ad-{h}")
            user = User(email=f"ad-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, user])
            await db.flush()
            ids["tenants"].append(int(t.id))
            ids["users"].append(int(user.id))
            ca = Case(title="ad-a", tenant_id=t.id)
            db.add(ca)
            await db.flush()
            ids["cases"].append(int(ca.id))
            gone = Artifact(tenant_id=t.id, value=f"10.1.0.{h[:2]}", artifact_type="ip")
            unlink = Artifact(tenant_id=t.id, value=f"10.2.0.{h[:2]}", artifact_type="ip", isolated=True)
            db.add_all([gone, unlink])
            await db.flush()
            ids["artifacts"] += [int(gone.id), int(unlink.id)]
            db.add_all([CaseArtifact(case_id=ca.id, artifact_id=gone.id),
                        CaseArtifact(case_id=ca.id, artifact_id=unlink.id)])
            ev = CaseEvent(tenant_id=t.id, case_id=ca.id, title="seen", event_date=datetime.now(timezone.utc))
            ev.artifacts = [gone, unlink]
            db.add(ev)
            await db.commit()
            uid = user.id
            app.dependency_overrides[deps.get_current_active_user] = lambda: user
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ids["tenants"][0]
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: user
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.delete(f"/api/v1/artifacts/{ids['artifacts'][0]}")
                assert r.status_code == 200, r.text
                r = await c.delete(f"/api/v1/artifacts/{ids['artifacts'][1]}/case/{ids['cases'][0]}")
                assert r.status_code == 200, r.text
            async with AsyncSessionLocal() as db2:
                left = (await db2.execute(select(Artifact.id).where(Artifact.id.in_(ids["artifacts"])))).all()
                assert left == [], left  # the isolated one is orphaned, so it goes too
                assert uid
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db3:
                await db3.execute(delete(CaseEvent).where(CaseEvent.case_id.in_(ids["cases"])))
                await db3.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids["cases"])))
                await db3.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(ids["cases"])))
                await db3.execute(delete(Artifact).where(Artifact.id.in_(ids["artifacts"])))
                await db3.execute(delete(Case).where(Case.id.in_(ids["cases"])))
                await db3.execute(delete(User).where(User.id.in_(ids["users"])))
                await db3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await db3.commit()
            await engine.dispose()


def test_artifact_delete_api(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
