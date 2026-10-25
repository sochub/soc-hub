"""Playbook drafts: hidden from non-admins, never applied/offered, published by admins."""
import asyncio

from sqlalchemy import delete, select

from app.api import deps
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.membership import TenantMembership
from app.models.playbook import PlaybookTemplate
from app.schemas.playbook import PlaybookTemplateCreate, TaskTemplateIn
from app.services import playbook_service
from tests.guest_fixtures import Ids, cleanup, client, make_case, make_tenant, make_user


def _run(body):
    async def main():
        await engine.dispose()
        ids = Ids()
        ids.templates = []
        try:
            async with AsyncSessionLocal() as db:
                await body(db, ids)
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db:
                await db.execute(delete(PlaybookTemplate).where(PlaybookTemplate.id.in_(ids.templates or [-1])))
                await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids.tenants or [-1]),
                                                        AuditLog.action == "playbook_published"))
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(ids.users or [-1])))
                await db.commit()
            await cleanup(ids)
            await engine.dispose()
    asyncio.run(main())


async def _setup(db, ids, monkeypatch, role):
    t = await make_tenant(db, ids)
    user = await make_user(db, ids)
    db.add(TenantMembership(user_id=user.id, tenant_id=t.id, role=role))
    await db.commit()
    await db.refresh(user, ["memberships"])
    user._active_tenant_id = t.id
    monkeypatch.setattr(deps, "_active_role", lambda u: role)
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: t.id
    return t, user


async def _draft(db, ids, t, user, status="draft"):
    pb = await playbook_service.create_template(
        db, tenant_id=t.id, user_id=user.id, status=status,
        data=PlaybookTemplateCreate(name="Drafty", category="phishing",
                                    tasks=[TaskTemplateIn(phase="containment", title="Isolate host")]))
    ids.templates.append(int(pb.id))
    return pb


def test_draft_hidden_from_analyst_list_and_get(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch, "analyst")
        pb = await _draft(db, ids, t, user)
        pub = await _draft(db, ids, t, user, status="published")
        async with client() as c:
            r = await c.get("/api/v1/playbooks/?include_drafts=true")
            got = [x["id"] for x in r.json()]
            assert r.status_code == 200 and pb.id not in got and pub.id in got
            assert (await c.get(f"/api/v1/playbooks/{pb.id}")).status_code == 404
    _run(body)


def test_admin_sees_drafts(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch, "admin")
        pb = await _draft(db, ids, t, user)
        async with client() as c:
            r = await c.get("/api/v1/playbooks/")
            assert pb.id not in [x["id"] for x in r.json()]  # no flag, no drafts
            r = await c.get("/api/v1/playbooks/?include_drafts=true")
            row = next(x for x in r.json() if x["id"] == pb.id)
            assert row["status"] == "draft"
            r = await c.get(f"/api/v1/playbooks/{pb.id}")
            assert r.status_code == 200 and r.json()["status"] == "draft"
    _run(body)


def test_apply_draft_refused(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch, "admin")
        app.dependency_overrides[deps.require_analyst_or_above] = lambda: user
        pb = await _draft(db, ids, t, user)
        case = await make_case(db, ids, t)
        async with client() as c:
            r = await c.post(f"/api/v1/cases/{case.id}/apply-playbook/{pb.id}")
            assert r.status_code == 404, r.text
    _run(body)


def test_publish_admin_only(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch, "analyst")
        pb = await _draft(db, ids, t, user)
        async with client() as c:
            assert (await c.post(f"/api/v1/playbooks/{pb.id}/publish")).status_code == 403
        await db.refresh(pb)
        assert pb.status == "draft"
    _run(body)


def test_publish_makes_it_applicable(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch, "admin")
        app.dependency_overrides[deps.require_analyst_or_above] = lambda: user
        pb = await _draft(db, ids, t, user)
        case = await make_case(db, ids, t)
        async with client() as c:
            r = await c.post(f"/api/v1/playbooks/{pb.id}/publish")
            assert r.status_code == 200 and r.json()["status"] == "published", r.text
            assert (await c.post(f"/api/v1/playbooks/{pb.id}/publish")).status_code == 200  # idempotent
            r = await c.post(f"/api/v1/cases/{case.id}/apply-playbook/{pb.id}")
            assert r.status_code in (200, 201), r.text
            assert (await c.post("/api/v1/playbooks/999999999/publish")).status_code == 404
        rows = (await db.execute(select(AuditLog).where(
            AuditLog.tenant_id == t.id, AuditLog.action == "playbook_published"))).scalars().all()
        assert len(rows) == 1 and rows[0].entity_id == pb.id
    _run(body)


def test_other_tenant_draft_cannot_be_published(monkeypatch):
    async def body(db, ids):
        t, user = await _setup(db, ids, monkeypatch, "admin")
        other = await make_tenant(db, ids)
        pb = await _draft(db, ids, other, user)
        async with client() as c:
            assert (await c.post(f"/api/v1/playbooks/{pb.id}/publish")).status_code == 404
    _run(body)
