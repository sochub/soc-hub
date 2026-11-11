import asyncio
import contextlib
import secrets

import httpx
from sqlalchemy import delete, select

from app.api import deps
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_attachment import CaseAttachment
from app.models.tenant import Tenant
from app.models.user import User

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\x1c\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


@contextlib.asynccontextmanager
async def _env(monkeypatch, tmp_path):
    """Two tenants (one case each) and a user; yields (client, case_id, other_tenant_case_id, state)."""
    await engine.dispose()
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    monkeypatch.setattr(deps, "_active_role", lambda u: "analyst")
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-inl")
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-inl2")
            user = User(email=f"inl-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            await db.commit()
            ca, cb = Case(title="inl-a", tenant_id=ids["tenants"][0]), Case(title="inl-b", tenant_id=ids["tenants"][1])
            db.add_all([ca, cb])
            await db.flush()
            ids["cases"] += [int(ca.id), int(cb.id)]
            await db.commit()
            state = {"tenant": ids["tenants"][0], "user": user}
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = lambda: state["user"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                yield client, ids["cases"][0], ids["cases"][1], state
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            cids, tids, uids = ids["cases"], ids["tenants"], ids["users"]
            if cids:
                art_ids = (await db.execute(select(CaseArtifact.artifact_id).where(
                    CaseArtifact.case_id.in_(cids)))).scalars().all()
                await db.execute(delete(CaseAttachment).where(CaseAttachment.case_id.in_(cids)))
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(cids)))
                await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(cids)))
                if art_ids:
                    await db.execute(delete(Artifact).where(Artifact.id.in_(art_ids)))
            if tids:
                await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            if cids:
                await db.execute(delete(Case).where(Case.id.in_(cids)))
            if uids:
                await db.execute(delete(User).where(User.id.in_(uids)))
            if tids:
                await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
    await engine.dispose()


def test_inline_upload_and_raw(monkeypatch, tmp_path):
    async def run():
        async with _env(monkeypatch, tmp_path) as (client, cid, cid_other, state):
            base = f"/api/v1/cases/{cid}/attachments"
            r = await client.post(base, files={"file": ("p.png", PNG, "image/png")}, data={"inline": "true"})
            assert r.status_code == 201, r.text
            assert r.json()["is_inline"] is True
            att = r.json()["id"]

            r = await client.get(f"{base}/{att}/raw")
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "image/png"
            assert r.headers["content-disposition"].startswith("inline")
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["content-security-policy"] == "sandbox"
            assert r.headers["cache-control"] == "private, max-age=300"
            assert r.content == PNG

            # pasted images are note content: no hash artifact, no activity-feed event
            assert await _side_effects(cid) == (0, 0)

            # inline reads are audit-exempt
            async with AsyncSessionLocal() as db:
                n = len((await db.execute(select(AuditLog).where(
                    AuditLog.tenant_id == state["tenant"], AuditLog.entity_id == att,
                    AuditLog.action == "download"))).scalars().all())
            assert n == 0

            assert [i["id"] for i in (await client.get(base)).json()["items"]] == []
            r = await client.get(base, params={"include_inline": "true"})
            assert [i["id"] for i in r.json()["items"]] == [att]

            # cross-tenant is a 404
            state["tenant"] = (await _other_tenant(cid_other))
            assert (await client.get(f"/api/v1/cases/{cid_other}/attachments/{att}/raw")).status_code == 404
            assert (await client.get(f"{base}/{att}/raw")).status_code == 404

    asyncio.run(run())


async def _side_effects(case_id):
    async with AsyncSessionLocal() as db:
        arts = (await db.execute(select(CaseArtifact).where(CaseArtifact.case_id == case_id))).scalars().all()
        evs = (await db.execute(select(TimelineEvent).where(
            TimelineEvent.case_id == case_id, TimelineEvent.event_type == "attachment_added"))).scalars().all()
        return len(arts), len(evs)


async def _other_tenant(case_id):
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Case.tenant_id).where(Case.id == case_id))).scalar_one()


def test_raw_rejects_non_inline(monkeypatch, tmp_path):
    async def run():
        async with _env(monkeypatch, tmp_path) as (client, cid, _o, _s):
            base = f"/api/v1/cases/{cid}/attachments"
            r = await client.post(base, files={"file": ("p.png", PNG, "image/png")})
            assert r.status_code == 201 and r.json()["is_inline"] is False
            assert await _side_effects(cid) == (1, 1)  # regular uploads keep artifact + timeline event
            assert (await client.get(f"{base}/{r.json()['id']}/raw")).status_code == 404

    asyncio.run(run())


def test_inline_rejects_non_image(monkeypatch, tmp_path):
    async def run():
        async with _env(monkeypatch, tmp_path) as (client, cid, _o, _s):
            r = await client.post(f"/api/v1/cases/{cid}/attachments",
                                  files={"file": ("a.txt", b"hello", "text/plain")}, data={"inline": "true"})
            assert r.status_code == 415, r.text

    asyncio.run(run())


def test_inline_rejects_content_not_matching_declared_image_and_audits_upload(monkeypatch, tmp_path):
    """N15: inline uploads skip the evidence hash/timeline, so the bytes must really be the declared image type;
    an accepted inline upload still writes an audit row."""
    async def run():
        async with _env(monkeypatch, tmp_path) as (client, cid, _o, state):
            base = f"/api/v1/cases/{cid}/attachments"
            for name, data, ctype in (("x.png", b"MZ\x90\x00 not a png", "image/png"),
                                      ("x.jpg", PNG, "image/jpeg"), ("x.webp", b"RIFF\x00\x00\x00\x00WAVE", "image/webp")):
                r = await client.post(base, files={"file": (name, data, ctype)}, data={"inline": "true"})
                assert r.status_code == 415, (name, r.text)
            r = await client.post(base, files={"file": ("p.png", PNG, "image/png")}, data={"inline": "true"})
            assert r.status_code == 201
            async with AsyncSessionLocal() as db:
                row = (await db.execute(select(AuditLog).where(
                    AuditLog.tenant_id == state["tenant"], AuditLog.entity_id == r.json()["id"],
                    AuditLog.action == "upload"))).scalars().one()
            assert row.changes["inline"] is True and row.changes["sha256"]

    asyncio.run(run())
