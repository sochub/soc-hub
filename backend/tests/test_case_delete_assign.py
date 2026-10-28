"""Case deletion (admin only, typed confirmation, preview warnings, what is kept) and optional task assignment
(assign, reassign, unassign; analysts/admins of the tenant only; notification), through the HTTP API and MCP."""
import asyncio
import secrets
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.mcp import tools
from app.models.artifact import Artifact, ArtifactType
from app.models.audit_log import AuditLog
from app.models.case import Alert, Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_attachment import CaseAttachment
from app.models.case_task import CaseTask
from app.models.containment import ContainmentAction
from app.models.ioc import IOC
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower, Notification
from app.models.tenant import Tenant
from app.models.user import User
from app.services import case_delete


def _as(user_id: int, tenant_id: int, role: str):
    """The current user as the role dependencies see it: a membership of the active tenant."""
    return SimpleNamespace(id=user_id, is_super_admin=False, is_active=True, _active_tenant_id=tenant_id,
                           memberships=[SimpleNamespace(tenant_id=tenant_id, role=role)])


async def _scenario(monkeypatch):
    removed = []

    async def fake_remove(keys):
        removed.extend(keys)
    monkeypatch.setattr(case_delete, "remove_evidence", fake_remove)

    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t, other = Tenant(name="cda", slug=f"cda-{h}"), Tenant(name="cda-o", slug=f"cda-o-{h}")
        people = {n: User(email=f"cda-{n}-{h}@example.test", full_name=n.title(), is_active=True,
                          hashed_password=get_password_hash(secrets.token_hex(8)))
                  for n in ("admin", "analyst", "mate", "viewer", "outsider")}
        db.add_all([t, other, *people.values()])
        await db.flush()
        u = {n: p.id for n, p in people.items()}
        db.add_all([TenantMembership(user_id=u["admin"], tenant_id=t.id, role="admin"),
                    TenantMembership(user_id=u["analyst"], tenant_id=t.id, role="analyst"),
                    TenantMembership(user_id=u["mate"], tenant_id=t.id, role="analyst"),
                    TenantMembership(user_id=u["viewer"], tenant_id=t.id, role="viewer"),
                    TenantMembership(user_id=u["outsider"], tenant_id=other.id, role="admin")])
        case = Case(title="Beacon to C2", tenant_id=t.id, owner_id=u["analyst"], slack_channel_id="C0WARROOM")
        db.add(case)
        await db.flush()
        art = Artifact(tenant_id=t.id, artifact_type=ArtifactType.IP, value="203.0.113.9")
        db.add(art)
        await db.flush()
        alert = Alert(tenant_id=t.id, source="edr", external_id=f"x-{h}", title="beacon", status="promoted",
                      case_id=case.id, payload={})
        ioc = IOC(tenant_id=t.id, case_id=case.id, value="203.0.113.9", ioc_type="ip")
        db.add_all([CaseArtifact(case_id=case.id, artifact_id=art.id), alert, ioc,
                    TimelineEvent(case_id=case.id, user_id=u["analyst"], event_type="comment", content="seen"),
                    CaseAttachment(tenant_id=t.id, case_id=case.id, filename="pcap.zip", storage_key=f"k-{h}",
                                   size_bytes=1, sha256="0" * 64, uploaded_by=u["analyst"]),
                    ContainmentAction(tenant_id=t.id, case_id=case.id, action="Isolate", target_kind="host",
                                      target="ws-01", status="active")])
        await db.commit()
        tid, oid, cid, art_id, alert_id, ioc_id = t.id, other.id, case.id, art.id, alert.id, ioc.id

    me = {"user": _as(u["analyst"], tid, "analyst")}
    app.dependency_overrides[deps.get_current_active_user] = lambda: me["user"]
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            # ── assignable people: analysts and admins of this tenant only ──
            names = [p["name"] for p in (await c.get("/api/v1/users/assignable")).json()]
            assert names == ["Admin", "Analyst", "Mate"], names

            # ── task assignment is optional ──
            r = await c.post(f"/api/v1/cases/{cid}/tasks", json={"title": "Isolate ws-01", "phase": "containment"})
            task = r.json()
            assert r.status_code == 201 and task["assignee_id"] is None, r.text
            r = await c.post(f"/api/v1/cases/{cid}/tasks", json={"title": "Pull logs", "assignee_id": u["mate"]})
            assert r.json()["assignee_name"] == "Mate", r.text
            put = lambda body: c.put(f"/api/v1/cases/{cid}/tasks/{task['id']}", json=body)  # noqa: E731
            r = await put({"assignee_id": u["mate"]})
            assert r.status_code == 200 and r.json()["assignee_id"] == u["mate"], r.text
            r = await put({"assignee_id": u["admin"]})  # reassign
            assert r.json()["assignee_name"] == "Admin"
            for bad in (u["viewer"], u["outsider"], 999999999):
                assert (await put({"assignee_id": bad})).status_code == 422
            r = await put({"title": "Isolate ws-01 now"})  # other edits leave the assignee alone
            assert r.json()["assignee_id"] == u["admin"]
            r = await put({"assignee_id": None})  # unassign
            assert r.json()["assignee_id"] is None
            async with AsyncSessionLocal() as db:
                notes = (await db.execute(select(Notification.user_id, Notification.summary).where(
                    Notification.case_id == cid, Notification.type == "assigned"))).all()
                timeline = list((await db.execute(select(TimelineEvent.content).where(
                    TimelineEvent.case_id == cid, TimelineEvent.event_type == "task"))).scalars())
            assert {n for n, _ in notes} == {u["mate"], u["admin"]} and all("assigned you task" in s for _, s in notes)
            assert "Task **Isolate ws-01** assigned to Admin" in timeline and "Task **Isolate ws-01 now** unassigned" in timeline

            # ── case reassignment ──
            r = await c.put(f"/api/v1/cases/{cid}", json={"owner_id": u["mate"]})
            assert r.status_code == 200 and r.json()["owner_id"] == u["mate"], r.text

            # ── delete: admins only, preview warns, confirm must repeat the id ──
            assert (await c.get(f"/api/v1/cases/{cid}/delete-preview")).status_code == 403
            assert (await c.delete(f"/api/v1/cases/{cid}", params={"confirm": cid})).status_code == 403
            me["user"] = _as(u["admin"], tid, "admin")
            pv = (await c.get(f"/api/v1/cases/{cid}/delete-preview")).json()
            assert (pv["tasks"], pv["evidence_files"], pv["alerts"], pv["iocs"], pv["artifacts"], pv["active_containment"],
                    pv["war_room"]) == (2, 1, 1, 1, 1, 1, True), pv
            w = " ".join(pv["warnings"])
            assert "still active" in w and "evidence file" in w and "Slack war room" in w and "back to the Alerts queue" in w
            assert (await c.delete(f"/api/v1/cases/{cid}", params={"confirm": cid + 1})).status_code == 422
            assert (await c.delete(f"/api/v1/cases/{cid}")).status_code == 422  # confirm is required
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: oid
            me["user"] = _as(u["outsider"], oid, "admin")
            assert (await c.delete(f"/api/v1/cases/{cid}", params={"confirm": cid})).status_code == 404  # other tenant
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
            me["user"] = _as(u["admin"], tid, "admin")
            assert (await c.delete(f"/api/v1/cases/{cid}", params={"confirm": cid})).status_code == 204
        assert removed == [f"k-{h}"]
        async with AsyncSessionLocal() as db:
            assert await db.get(Case, cid) is None
            assert (await db.execute(select(CaseTask).where(CaseTask.case_id == cid))).first() is None
            a = await db.get(Alert, alert_id)
            assert a.case_id is None and a.status == "pending"
            assert (await db.get(IOC, ioc_id)).case_id is None and await db.get(Artifact, art_id) is not None
            audit = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "case_deleted",
                                                             AuditLog.entity_id == cid))).scalars().one()
            assert audit.changes["title"] == "Beacon to C2" and audit.user_id == u["admin"]

        # MCP can assign tasks but never delete a case
        assert not hasattr(tools, "delete_case")
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            for model in (Notification, CaseFollower, TimelineEvent, CaseTask, CaseArtifact, CaseAttachment, ContainmentAction):
                await db.execute(delete(model).where(model.case_id == cid))
            await db.execute(delete(Alert).where(Alert.id == alert_id))
            await db.execute(delete(IOC).where(IOC.id == ioc_id))
            await db.execute(delete(Artifact).where(Artifact.id == art_id))
            await db.execute(delete(Case).where(Case.id == cid))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([tid, oid])))
            await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_([tid, oid])))
            await db.execute(delete(User).where(User.id.in_(list(u.values()))))
            await db.execute(delete(Tenant).where(Tenant.id.in_([tid, oid])))
            await db.commit()
        await engine.dispose()


def test_case_delete_and_task_assignment(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
