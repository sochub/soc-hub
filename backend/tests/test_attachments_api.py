import asyncio
import hashlib
import io
import logging
import re
import secrets

import httpx
import pyzipper
from fastapi import HTTPException
from sqlalchemy import delete, func, select

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

LOGGER = "app.api.v1.attachments"
LOG_RX = re.compile(r"^attachment (upload|download|delete) tenant=\S+ case=\S+ id=\S+ size=\S+ malicious=\S+ "
                    r"outcome=[a-z_]+$")
NAMES = ("e vil.txt", "ab.txt", "a\nb.txt", "x.exe", "big.bin", "empty.txt", "v.txt")


def _raw_multipart(filename: bytes, body: bytes) -> bytes:
    return (b'--B\r\nContent-Disposition: form-data; name="file"; filename="' + filename +
            b'"\r\nContent-Type: text/plain\r\n\r\n' + body + b"\r\n--B--\r\n")


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario(monkeypatch, caplog, tmp_path):
    await engine.dispose()
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 1)
    h = secrets.token_hex(3)
    ids = {"tenants": [], "users": [], "cases": []}
    state = {"tenant": None, "role": "analyst", "user": None}
    monkeypatch.setattr(deps, "_active_role", lambda u: state["role"])
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-attapi")
            t2 = Tenant(name="wf-test", slug=f"wf-test-{h}-attapi2")
            user = User(email=f"att-api-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True)
            db.add_all([t, t2, user])
            await db.flush()
            ids["tenants"] += [int(t.id), int(t2.id)]
            ids["users"].append(int(user.id))
            await db.commit()
            tid, tid2 = ids["tenants"]
            ca, cb = Case(title="att-a", tenant_id=tid), Case(title="att-b", tenant_id=tid2)
            db.add_all([ca, cb])
            await db.flush()
            ids["cases"] += [int(ca.id), int(cb.id)]
            await db.commit()
            cid, cid2 = ids["cases"]
            uid, email = int(user.id), user.email

            state["tenant"], state["user"] = tid, user
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: state["tenant"]
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["role"] in ("admin", "analyst") else _deny())

            def att_lines():
                return [rec.getMessage() for rec in caplog.records if rec.name == LOGGER]

            base = f"/api/v1/cases/{cid}/attachments"

            async def rows():
                async with AsyncSessionLocal() as db2:
                    return (await db2.execute(select(func.count()).select_from(CaseAttachment).where(
                        CaseAttachment.case_id == cid))).scalar()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                async def call(method, url, op=None, outcome=None, **kw):
                    # l) exactly one well-formed line per upload/download/delete call; none for anything else
                    n0 = len(att_lines())
                    r = await client.request(method, url, **kw)
                    new = att_lines()[n0:]
                    if op is None:
                        assert new == [], f"l unexpected log {new}"
                    else:
                        assert len(new) == 1 and LOG_RX.match(new[0]), f"l {op} {new}"
                        assert new[0].startswith(f"attachment {op} tenant={state['tenant']} "), f"l {op} {new}"
                        assert new[0].endswith(f"outcome={outcome}"), f"l outcome {new}"
                    return r

                # a) upload: name sanitised, size and hash right, storage_key never exposed
                r = await call("POST", base, "upload", "ok", files={"file": ("../e vil.txt", b"hello", "text/plain")})
                assert r.status_code == 201, f"a {r.status_code} {r.text}"
                a1 = r.json()
                sha = hashlib.sha256(b"hello").hexdigest()
                assert a1["filename"] == "e vil.txt", f"a filename {a1}"
                assert a1["size_bytes"] == 5 and a1["sha256"] == sha, f"a size/sha {a1}"
                assert a1["is_malicious"] is False and "storage_key" not in a1, f"a {a1}"
                assert a1["case_id"] == cid and a1["uploaded_by"] == uid and a1["uploaded_by_email"] == email, "a who"
                att1 = a1["id"]

                # b) list: max_upload_mb plus items newest first
                # d) a raw CR/LF filename is rejected by the multipart parser before the handler (httpx would
                #    percent-encode it, so the body is built by hand); a raw LF reaches the handler and is stripped
                n_rows = await rows()
                r = await call("POST", base, content=_raw_multipart(b"a\r\nb.txt", b"second"),
                               headers={"content-type": "multipart/form-data; boundary=B"})
                assert r.status_code == 400 and await rows() == n_rows, f"d raw CRLF {r.status_code} {r.text}"
                r = await call("POST", base, "upload", "ok", content=_raw_multipart(b"a\nb.txt", b"second"),
                               headers={"content-type": "multipart/form-data; boundary=B"})
                assert r.status_code == 201 and r.json()["filename"] == "ab.txt", f"b/d upload {r.text}"
                att2 = r.json()["id"]
                r = await call("GET", base)
                assert r.status_code == 200, f"b {r.status_code}"
                d = r.json()
                assert d["max_upload_mb"] == 1, f"b {d}"
                assert [i["id"] for i in d["items"]] == [att2, att1], f"b order {d}"
                assert all("storage_key" not in i for i in d["items"]), "b storage_key"

                # c) download: exact safe headers, original bytes
                r = await call("GET", f"{base}/{att1}/download", "download", "ok")
                assert r.status_code == 200 and r.content == b"hello", f"c {r.status_code}"
                assert r.headers["content-type"] == "application/octet-stream", f"c {r.headers}"
                assert r.headers["x-content-type-options"] == "nosniff", "c nosniff"
                assert r.headers["content-security-policy"] == "sandbox", "c csp"
                assert r.headers["cache-control"] == "no-store", "c cache"
                assert r.headers["x-content-sha256"] == sha, "c sha"
                assert r.headers["content-disposition"] == "attachment; filename*=UTF-8''e%20vil.txt", "c cd"

                # d) CR/LF in the upload name never reaches the header
                r = await call("GET", f"{base}/{att2}/download", "download", "ok")
                assert r.status_code == 200 and r.content == b"second", f"d {r.status_code}"
                cd = r.headers["content-disposition"]
                assert "\r" not in cd and "\n" not in cd, f"d {cd!r}"
                assert cd == "attachment; filename*=UTF-8''ab.txt", f"d {cd!r}"

                # e) malicious upload is served as an encrypted zip
                r = await call("POST", base, "upload", "ok", data={"is_malicious": "true"},
                               files={"file": ("x.exe", b"MZ..", "application/x-msdownload")})
                assert r.status_code == 201 and r.json()["is_malicious"] is True, f"e {r.text}"
                att3 = r.json()["id"]
                r = await call("GET", f"{base}/{att3}/download", "download", "ok")
                assert r.status_code == 200, f"e {r.status_code}"
                assert r.headers["content-type"] == "application/octet-stream", "e content-type"
                assert r.headers["content-disposition"] == "attachment; filename*=UTF-8''x.exe.zip", "e cd"
                with pyzipper.AESZipFile(io.BytesIO(r.content)) as zf:
                    zf.setpassword(b"infected")
                    assert zf.read("x.exe") == b"MZ..", "e zip body"

                # f) over the limit -> 413, no row, no file left behind
                before_rows, before_files = await rows(), set(tmp_path.rglob("*"))
                r = await call("POST", base, "upload", "too_large",
                               files={"file": ("big.bin", b"x" * (1024 * 1024 + 1), "application/octet-stream")})
                assert r.status_code == 413 and r.json()["detail"] == "File exceeds 1 MB", f"f {r.status_code} {r.text}"
                assert await rows() == before_rows, "f row added"
                assert set(tmp_path.rglob("*")) == before_files, "f files added"

                # g) empty file -> 422
                r = await call("POST", base, "upload", "empty", files={"file": ("empty.txt", b"", "text/plain")})
                assert r.status_code == 422 and r.json()["detail"] == "Empty file", f"g {r.status_code} {r.text}"
                assert await rows() == before_rows, "g row added"
                assert set(tmp_path.rglob("*")) == before_files, "g files added"

                # h) viewer: writes 403, reads still 200
                state["role"] = "viewer"
                r = await call("POST", base, files={"file": ("v.txt", b"v", "text/plain")})
                assert r.status_code == 403, f"h post {r.status_code}"
                r = await call("DELETE", f"{base}/{att2}")
                assert r.status_code == 403, f"h delete {r.status_code}"
                r = await call("GET", base)
                assert r.status_code == 200 and len(r.json()["items"]) == 3, f"h list {r.status_code}"
                r = await call("GET", f"{base}/{att2}/download", "download", "ok")
                assert r.status_code == 200 and r.content == b"second", f"h download {r.status_code}"
                state["role"] = "analyst"

                # i) tenant B cannot see tenant A's case or attachments
                state["tenant"] = tid2
                r = await call("GET", base)
                assert r.status_code == 404, f"i list {r.status_code}"
                r = await call("GET", f"/api/v1/cases/{cid2}/attachments/{att1}/download")
                assert r.status_code == 404, f"i via B case {r.status_code}"
                r = await call("GET", f"{base}/{att1}/download")
                assert r.status_code == 404, f"i A case as B {r.status_code}"
                r = await call("DELETE", f"{base}/{att1}")
                assert r.status_code == 404, f"i delete as B {r.status_code}"
                state["tenant"] = tid

                # j) soft delete
                r = await call("DELETE", f"{base}/{att1}", "delete", "ok")
                assert r.status_code == 204, f"j {r.status_code} {r.text}"
                r = await call("GET", base)
                assert att1 not in [i["id"] for i in r.json()["items"]], "j still listed"
                r = await call("GET", f"{base}/{att1}/download")
                assert r.status_code == 404, f"j download {r.status_code}"
                r = await call("DELETE", f"{base}/{att1}")
                assert r.status_code == 404, f"j delete again {r.status_code}"
                async with AsyncSessionLocal() as db2:
                    ev = (await db2.execute(select(TimelineEvent.event_type).where(
                        TimelineEvent.case_id == cid))).scalars().all()
                    assert "attachment_deleted" in ev, f"j timeline {ev}"
                    acts = (await db2.execute(select(AuditLog.action).where(
                        AuditLog.entity_type == "attachment", AuditLog.entity_id == att1,
                        AuditLog.tenant_id == tid).order_by(AuditLog.id))).scalars().all()
                    assert acts == ["upload", "download", "delete"], f"j audit {acts}"
                    row = (await db2.execute(select(CaseAttachment).where(CaseAttachment.id == att1))).scalars().one()
                    assert row.deleted_at is not None and row.deleted_by == uid, "j row not soft-deleted"

                # k) include_deleted: analyst 403, admin sees the deleted row with deleted_by_email
                r = await call("GET", base, params={"include_deleted": "true"})
                assert r.status_code == 403 and r.json()["detail"] == "Admins only", f"k analyst {r.status_code}"
                state["role"] = "admin"
                r = await call("GET", base, params={"include_deleted": "true"})
                assert r.status_code == 200, f"k admin {r.status_code}"
                dele = [i for i in r.json()["items"] if i["id"] == att1]
                assert dele and dele[0]["deleted_at"] and dele[0]["deleted_by"] == uid, f"k {r.json()}"
                assert dele[0]["deleted_by_email"] == email, f"k email {dele}"
                state["role"] = "analyst"

            # l) no file name in any log record
            msgs = [rec.getMessage() for rec in caplog.records]
            assert all(n not in m for n in NAMES for m in msgs), "l file name logged"
            assert len(att_lines()) == 10, f"l total {att_lines()}"
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
            left = (await db.execute(select(func.count()).select_from(Tenant).where(
                Tenant.id.in_(tids or [-1])))).scalar()
            assert left == 0, f"leaked tenants {tids}"
    await engine.dispose()


def test_attachments_api(monkeypatch, caplog, tmp_path):
    caplog.set_level(logging.INFO)
    asyncio.run(_scenario(monkeypatch, caplog, tmp_path))
