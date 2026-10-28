"""Importing a marketplace playbook links its catalog tasks to the tenant's installed workflow (by template name)."""
import asyncio
import secrets

import httpx
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.playbook import PlaybookTaskTemplate, PlaybookTemplate
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import Workflow
from app.scripts.seed_playbooks import CATALOG

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "config": {}}], "edges": []}
NAME = "Isolate a device (Microsoft Defender)"


async def _scenario():
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t, other = Tenant(name="pb-link", slug=f"pb-link-{h}"), Tenant(name="pb-other", slug=f"pb-other-{h}")
        u = User(email=f"pb-link-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, other, u])
        await db.flush()
        entry = next(c for c in CATALOG if c["name"] == NAME)
        src = PlaybookTemplate(tenant_id=None, is_system=True, name=NAME, category=entry["category"], description="d")
        db.add(src)
        await db.flush()
        for i, (phase, title, desc, *_) in enumerate(entry["tasks"]):
            db.add(PlaybookTaskTemplate(template_id=src.id, phase=phase, title=title, description=desc, order=i))
        mine = Workflow(tenant_id=t.id, name="Microsoft Defender: isolate device", trigger_type="manual", graph=GRAPH)
        theirs = Workflow(tenant_id=other.id, name="Microsoft Defender: release device from isolation",
                          trigger_type="manual", graph=GRAPH)  # another tenant's workflow is never linked
        db.add_all([mine, theirs])
        await db.commit()
        tid, oid, uid, src_id, mine_id = t.id, other.id, u.id, src.id, mine.id
    try:
        app.dependency_overrides[deps.get_current_active_user] = lambda: u
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
        app.dependency_overrides[deps.require_admin] = lambda: u
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/playbooks/import", json={"template_ids": [src_id]})
            assert r.status_code in (200, 201) and r.json()["imported"] == 1, r.text
            copy_id = r.json()["imported_ids"][0]
        async with AsyncSessionLocal() as db:
            links = {x.title: x.workflow_id for x in (await db.execute(
                select(PlaybookTaskTemplate).where(PlaybookTaskTemplate.template_id == copy_id))).scalars()}
        assert links.pop("Isolate the device in Defender") == mine_id
        assert set(links.values()) == {None}, links  # release isn't installed here; other tasks have no workflow
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(PlaybookTemplate).where(PlaybookTemplate.tenant_id == tid))
            await db.execute(delete(PlaybookTemplate).where(PlaybookTemplate.id == src_id))
            await db.execute(delete(Workflow).where(Workflow.tenant_id.in_([tid, oid])))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id.in_([tid, oid])))
            await db.commit()
        await engine.dispose()


def test_import_links_catalog_tasks_to_installed_workflows():
    asyncio.run(_scenario())
