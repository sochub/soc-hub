import importlib.util
import pathlib
import secrets
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

import app.db.base  # noqa: F401
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.enrichment.indicators import normalise
from app.main import app
from app.models.artifact import Artifact, ArtifactType
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.tenant import Tenant
from app.models.user import User
from app.workflows.context import case_to_dict


def _migration():
    path = next(pathlib.Path(__file__).resolve().parents[1].glob("alembic/versions/*_artifact_hostname_username.py"))
    spec = importlib.util.spec_from_file_location("artifact_hostname_username_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enum_has_hostname_and_username():
    assert ArtifactType("hostname") is ArtifactType.HOSTNAME and ArtifactType("username") is ArtifactType.USERNAME


def test_new_types_are_not_enrichable():
    assert normalise("hostname", "web-01") is None and normalise("username", "jdoe") is None


@pytest.mark.asyncio
async def test_add_hostname_and_username_artifacts_and_context_attributes():
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t = Tenant(name="bt", slug=f"bt-{h}")
        user = User(email=f"bt-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, user])
        await db.flush()
        case = Case(title="c", tenant_id=t.id)
        db.add(case)
        await db.commit()
        tid, cid, uid = t.id, case.id, user.id
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.require_analyst_or_above] = lambda: user
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            a = "/api/v1/artifacts/"
            r = await c.post(a, json={"case_id": cid, "artifact_type": "hostname", "value": "web-01"})
            assert r.status_code in (200, 201) and r.json()["artifact_type"] == "hostname", r.text
            r = await c.post(a, json={"case_id": cid, "artifact_type": "username", "value": "jdoe"})
            assert r.status_code in (200, 201) and r.json()["artifact_type"] == "username", r.text
            listed = {(x["artifact_type"], x["value"]) for x in (await c.get(f"{a}case/{cid}")).json()}
            assert listed == {("hostname", "web-01"), ("username", "jdoe")}, listed
        async with AsyncSessionLocal() as db:
            ctx = await case_to_dict(db, await db.get(Case, cid))
        assert ctx["attributes"]["username"] == "jdoe" and ctx["attributes"]["hostname"] == "web-01"
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            art_ids = (await db.execute(select(Artifact.id).where(Artifact.tenant_id == tid))).scalars().all()
            await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == cid))
            if art_ids:
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "artifact", AuditLog.entity_id.in_(art_ids)))
                await db.execute(delete(Artifact).where(Artifact.id.in_(art_ids)))
            await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == cid))
            await db.execute(delete(Case).where(Case.id == cid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.execute(delete(User).where(User.id == uid))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_custom_type_key_clash_renamed():
    slug = f"bc-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        t = Tenant(name=slug, slug=slug)
        db.add(t)
        await db.flush()
        d = ArtifactTypeDefinition(tenant_id=t.id, key="username", label="Username")
        keep = ArtifactTypeDefinition(tenant_id=t.id, key="port", label="Port")
        db.add_all([d, keep])
        await db.flush()
        art = Artifact(tenant_id=t.id, artifact_type=ArtifactType.OTHER, value="bob", custom_type_id=d.id)
        db.add(art)
        await db.commit()
        tid, did, kid, aid = t.id, d.id, keep.id, art.id
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: _migration().rename_clashing_custom_types(sync_conn))
        async with AsyncSessionLocal() as db:
            assert (await db.get(ArtifactTypeDefinition, did)).key == "username_custom"
            assert (await db.get(ArtifactTypeDefinition, kid)).key == "port"
            assert (await db.get(Artifact, aid)).custom_type_id == did
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Artifact).where(Artifact.id == aid))
            await db.execute(delete(ArtifactTypeDefinition).where(ArtifactTypeDefinition.id.in_([did, kid])))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()
