"""Artifact update/delete/unlink, note delete and playbook update/delete work over REST and leave audit rows."""
import asyncio
import secrets
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select

from app.api import deps
from app.api.v1 import cases as cases_api
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.audit_log import AuditLog
from app.models.case_event import CaseEvent
from app.models.playbook import PlaybookTemplate
from app.models.tenant import Tenant
from app.models.user import User


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": [], "artifacts": []}
    monkeypatch.setattr(deps, "_active_role", lambda u: "analyst")
    monkeypatch.setattr(cases_api, "resolve_active_role", lambda *a: "analyst")  # the note author may delete
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
            note = TimelineEvent(case_id=ca.id, user_id=user.id, event_type="comment", content="draft note")
            pb = PlaybookTemplate(tenant_id=t.id, name="pb", category="other", is_system=False, status="published")
            db.add_all([ev, note, pb])
            await db.commit()
            note_id, pb_id = note.id, pb.id
            await db.refresh(user, ["memberships"])  # routes read it outside the session
            uid = user.id
            app.dependency_overrides[deps.get_current_active_user] = lambda: user
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ids["tenants"][0]
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: user
            app.dependency_overrides[deps.require_admin] = lambda: user
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                r = await c.put(f"/api/v1/artifacts/{ids['artifacts'][0]}", json={"description": "seen in proxy"})
                assert r.status_code == 200, r.text
                # a case of another tenant (or none) can't be used to unlink
                assert (await c.delete(f"/api/v1/artifacts/{ids['artifacts'][1]}/case/0")).status_code == 404
                r = await c.delete(f"/api/v1/cases/{ids['cases'][0]}/timeline/{note_id}")
                assert r.status_code == 200, r.text
                r = await c.put(f"/api/v1/playbooks/{pb_id}", json={"name": "pb2"})
                assert r.status_code == 200, r.text
                assert (await c.delete(f"/api/v1/playbooks/{pb_id}")).status_code == 204
                r = await c.delete(f"/api/v1/artifacts/{ids['artifacts'][0]}")
                assert r.status_code == 200, r.text
                r = await c.delete(f"/api/v1/artifacts/{ids['artifacts'][1]}/case/{ids['cases'][0]}")
                assert r.status_code == 200, r.text
            async with AsyncSessionLocal() as db2:
                left = (await db2.execute(select(Artifact.id).where(Artifact.id.in_(ids["artifacts"])))).all()
                assert left == [], left  # the isolated one is orphaned, so it goes too
                rows = (await db2.execute(select(AuditLog.entity_type, AuditLog.action, AuditLog.changes).where(
                    AuditLog.tenant_id == ids["tenants"][0], AuditLog.user_id == uid))).all()
                got = {(e, a) for e, a, _ in rows}
                assert {("artifact", "update"), ("artifact", "delete"), ("case", "artifact_removed"),
                        ("case", "note_deleted"), ("playbook", "playbook_updated"), ("playbook", "playbook_deleted")} <= got, got
                upd = next(ch for e, a, ch in rows if (e, a) == ("artifact", "update"))
                assert upd["description"] == {"from": None, "to": "seen in proxy"}, upd
                assert next(ch for e, a, ch in rows if a == "note_deleted")["content"] == "draft note"
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db3:
                await db3.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"]),
                                                         AuditLog.user_id.in_(ids["users"])))
                await db3.execute(delete(PlaybookTemplate).where(PlaybookTemplate.tenant_id.in_(ids["tenants"])))
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
