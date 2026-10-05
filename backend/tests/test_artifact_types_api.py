import secrets

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

import app.db.base  # noqa: F401
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.tenant import Tenant
from app.models.user import User


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


@pytest.mark.asyncio
async def test_artifact_types_crud_and_private_filtering():
    h = secrets.token_hex(3)
    state = {"role": "admin"}
    async with AsyncSessionLocal() as db:
        t, other = Tenant(name="at", slug=f"at-{h}"), Tenant(name="at2", slug=f"at2-{h}")
        user = User(email=f"at-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, other, user])
        await db.flush()
        case = Case(title="c", tenant_id=t.id)
        db.add(case)
        await db.commit()
        tid, oid, cid, uid = t.id, other.id, case.id, user.id
    state["tenant"] = tid
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.require_admin] = lambda: user if state["role"] == "admin" else _deny()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            u = "/api/v1/artifact-types/"
            # validation
            assert (await c.post(u, json={"key": "ip", "label": "x"})).status_code == 400, "builtin collision"
            assert (await c.post(u, json={"key": "Bad Key", "label": "x"})).status_code == 422, "regex"
            assert (await c.post(u, json={"key": "k1", "label": "x", "payload_key": "a..b"})).status_code == 422, "path"
            r = await c.post(u, json={"key": "username", "label": "Username", "payload_key": "user_name"})
            assert r.status_code == 201, r.text
            uname = r.json()
            assert uname["show_in_mindmap"] is True and uname["private"] is False
            assert (await c.post(u, json={"key": "username", "label": "dup"})).status_code == 409, "dup"
            r = await c.post(u, json={"key": "secret_id", "label": "Secret", "private": True, "payload_key": ""})
            secret = r.json()
            assert secret["payload_key"] is None
            # update: key immutable, label changes
            r = await c.put(f"{u}{uname['id']}", json={"label": "User name", "key": "zzz"})
            assert r.status_code == 200 and r.json()["label"] == "User name" and r.json()["key"] == "username"
            assert [d["key"] for d in (await c.get(u)).json()] == ["username", "secret_id"]
            # non-admin
            state["role"] = "viewer"
            assert (await c.post(u, json={"key": "x1", "label": "x"})).status_code == 403
            state["role"] = "admin"

            # artifacts with custom keys
            a = "/api/v1/artifacts/"
            assert (await c.post(a, json={"case_id": cid, "artifact_type": "nope", "value": "v"})).status_code == 422
            r1 = await c.post(a, json={"case_id": cid, "artifact_type": "username", "value": "bob"})
            assert r1.status_code == 200 and r1.json()["artifact_type"] == "username", r1.text
            r2 = await c.post(a, json={"case_id": cid, "artifact_type": "other", "value": "bob"})
            assert r2.json()["id"] != r1.json()["id"] and r2.json()["artifact_type"] == "other", "no cross-type dedupe"
            r3 = await c.post(a, json={"case_id": cid, "artifact_type": "secret_id", "value": "s3cr3t"})
            assert r3.status_code == 200
            listed = {(x["artifact_type"], x["value"]) for x in (await c.get(f"{a}case/{cid}")).json()}
            assert listed == {("username", "bob"), ("other", "bob")}, listed
            assert "s3cr3t" not in (await c.get(a)).text
            assert (await c.get(f"{a}search/", params={"value": "s3cr3t"})).json() == []
            # retype via update
            r = await c.put(f"{a}{r2.json()['id']}", json={"artifact_type": "username"})
            assert r.status_code == 200 and r.json()["artifact_type"] == "username", r.text
            assert (await c.put(f"{a}{r2.json()['id']}", json={"artifact_type": "nope"})).status_code == 422
            # private artifacts are workflow-only: by-id endpoints act as if they don't exist
            pid = r3.json()["id"]
            assert (await c.put(f"{a}{pid}", json={})).status_code == 404, "PUT must not echo a private value"
            assert (await c.delete(f"{a}{pid}")).status_code == 404
            assert (await c.delete(f"{a}{pid}/case/{cid}")).status_code == 404

            # promote needs analyst+ (it now writes artifacts)
            app.dependency_overrides[deps.require_analyst_or_above] = _deny
            assert (await c.post(f"/api/v1/alerts/999999/promote/{cid}")).status_code == 403
            del app.dependency_overrides[deps.require_analyst_or_above]

            # delete in use -> 409; cross-tenant -> 404
            assert (await c.delete(f"{u}{uname['id']}")).status_code == 409
            state["tenant"] = oid
            assert (await c.delete(f"{u}{secret['id']}")).status_code == 404
            state["tenant"] = tid

        async with AsyncSessionLocal() as db:
            texts = (await db.execute(select(TimelineEvent.content).where(TimelineEvent.case_id == cid))).scalars().all()
            assert not any("s3cr3t" in (x or "") for x in texts), texts
            assert any("bob (username)" in (x or "") for x in texts), texts
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
            await db.execute(delete(ArtifactTypeDefinition).where(ArtifactTypeDefinition.tenant_id.in_([tid, oid])))
            await db.execute(delete(Tenant).where(Tenant.id.in_([tid, oid])))
            await db.execute(delete(User).where(User.id == uid))
            await db.commit()
        await engine.dispose()
