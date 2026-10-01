# Evidence Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Analysts upload evidence files to a case. Each file gets a server-computed SHA-256, an audit trail and soft delete. Downloads are always safe, and malicious samples are wrapped in an AES ZIP with password `infected`.

**Architecture:**
- **Storage:** a small storage interface (`app/storage/`) with Local and S3 implementations, chosen by settings.
- **Upload pipeline:** an attachments service (`app/services/attachments.py`). It streams the upload, hashes it, enforces the size limit, optionally ZIPs it, stores it, and then writes the row, the `file_hash` artifact, the timeline event and the audit entry.
- **API:** an attachments router nested under cases.
- **Frontend:** an Evidence tab.
- **Proxy:** nginx is templated so the upload limit follows `MAX_UPLOAD_MB`.

**Tech Stack:** FastAPI (`UploadFile`, `StreamingResponse`), SQLAlchemy async, Alembic, boto3, pyzipper; React 19 + TanStack Query + axios.

**Spec:** `docs/superpowers/specs/2026-10-02-evidence-attachments-design.md`

## Global Constraints

- **Branch:** `feat/evidence-attachments`, stacked on `feat/threat-intel`.
- **Migration:** revision `a5b6c7d8e9f0`, with `down_revision = "f4a5b6c7d8e9"`.
- **Settings:**

  | Setting | Value |
  |---|---|
  | `STORAGE_BACKEND` | `Literal["local","s3"]`, default `"local"` |
  | `STORAGE_LOCAL_PATH` | `"/data/attachments"` |
  | `S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT_URL` | Optional, default `None` |
  | `S3_PREFIX` | `""` |
  | `MAX_UPLOAD_MB` | `100`, range 1–5120 |

  With `s3` and no bucket, startup fails with `ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")`.
- **Storage key:** `tenants/{tenant_id}/cases/{case_id}/{uuid4hex}`. The server generates it, it never appears in API output, and it never comes from user input.
- **Reading uploads:** chunks of 64 KiB (`65536`).
- **Malicious ZIP:** `pyzipper.AESZipFile`, `compression=ZIP_DEFLATED`, `encryption=WZ_AES`. The password is `b"infected"` and the single member is named with the sanitised filename. `sha256` and `size_bytes` always describe the ORIGINAL bytes.
- **Download headers, sent on every download:**
  - `Content-Type: application/octet-stream`
  - `X-Content-Type-Options: nosniff`
  - `Content-Security-Policy: sandbox`
  - `Cache-Control: no-store`
  - `X-Content-SHA256: <sha256>`
  - `Content-Disposition: attachment; filename*=UTF-8''<quote(name)>`. Malicious files get the name `<name>.zip`.
- **Errors:**

  | Status | Detail |
  |---|---|
  | 413 | `"File exceeds {MAX_UPLOAD_MB} MB"` |
  | 422 | `"Empty file"` |
  | 404 | Unknown case, case in another tenant, unknown attachment, or deleted attachment |
  | 403 | A non-admin passes `include_deleted=true` |

- **Roles:**
  - upload and delete: `require_analyst_or_above`
  - list and download: `get_current_active_user`
  - `include_deleted`: admin only, using `app.utils.roles.resolve_active_role(user)` equal to `"admin"`, or `user.is_super_admin`.
- **Audit:** `create_audit_log(db, entity_type="attachment", entity_id=id, action="upload"|"download"|"delete", tenant_id, user_id, changes={"filename","sha256","is_malicious","case_id"})`.
- **Timeline:** events `attachment_added`, with content `f"Attached {filename} (sha256 {sha256[:12]}…)"`, and `attachment_deleted`, with content `f"Deleted attachment {filename}"`.
- **Logging:** exactly one INFO line per operation, from logger `app.api.v1.attachments`: `attachment <op> tenant=%s case=%s id=%s size=%s malicious=%s outcome=%s`. File names and contents are never logged.
- **Tests:**
  - No network and no real S3.
  - Set `STORAGE_LOCAL_PATH` to a pytest `tmp_path` via monkeypatch.
  - DB rows are deleted by exact ids. Call `await engine.dispose()` on entry and on exit.
  - The `wf-test-%` tenant count must be 0 after the full suite.
  - The existing conftest (`tests/conftest.py`) already blocks enrichment enqueues.
- **Commands:**
  - Tests: `docker compose exec -T backend python -m pytest tests/<file> -q`.
  - After backend changes: `docker restart case_management-backend-1 case_management-worker-1`.
  - After `requirements.txt` changes: `docker compose build backend worker && docker compose up -d --no-deps backend worker`.
- **Commits:** use `perl -e 'alarm 60; exec @ARGV' git commit ...`, ending with the agent's own Co-Authored-By line.

## Review Focus

1. **Upload just over the limit.** A file of `MAX_UPLOAD_MB*1MiB + 1` bytes must get 413, leave no stored object (including `.part` files) and write no DB row. → Task 3 test.
2. **Hostile filenames.** `../../etc/passwd`, `C:\x\y.exe`, CR/LF, NUL, 300-character names and `""` must never affect the storage path or headers, and the download `Content-Disposition` must stay a single valid header line. → Task 2 and Task 3 tests.
3. **The same file uploaded twice.** It must produce two attachments but only ONE `file_hash` artifact. → Task 3 test.
4. **Commit failure after the file is stored.** The stored object must be deleted (best effort). → Task 2 test.
5. **Viewer or other-tenant access.** A viewer gets 403 on POST and DELETE. Another tenant's case or attachment id gets 404 on every endpoint. → Task 3 test.

---

## File structure

```
backend/requirements.txt                          + pyzipper==<pinned>                    (T1)
backend/app/core/config.py                        STORAGE_*, S3_*, MAX_UPLOAD_MB           (T1)
backend/app/storage/__init__.py                   get_storage(), StorageError              (T1)
backend/app/storage/local.py, s3.py               LocalStorage, S3Storage                  (T1)
backend/app/models/case_attachment.py             CaseAttachment                           (T2)
backend/alembic/versions/a5b6c7d8e9f0_case_attachments.py                                  (T2)
backend/app/services/attachments.py               sanitize_filename, store_upload, ...    (T2)
backend/app/schemas/attachment.py, backend/app/api/v1/attachments.py                      (T3)
docker-compose.yml, frontend/nginx.conf → frontend/nginx/default.conf.template, frontend/Dockerfile (T4)
frontend/src/features/cases/CaseEvidence.tsx, CaseDetail.tsx, types/index.ts               (T5)
docs/configuration.md, docs/features.md                                                    (T6)
```

---

### Task 1: Settings, dependency and storage backends

**Files:**
- Modify: `backend/requirements.txt`, `backend/app/core/config.py`
- Create: `backend/app/storage/__init__.py`, `backend/app/storage/local.py`, `backend/app/storage/s3.py`
- Test: `backend/tests/test_storage.py`

**Interfaces:**
- Produces:
  - `class StorageError(Exception)`
  - `LocalStorage(root: str)` and `S3Storage(bucket, region=None, endpoint_url=None, prefix="")`, each with:
    - `async put(key: str, chunks: AsyncIterator[bytes]) -> int`
    - `async open(key: str) -> AsyncIterator[bytes]` (an async generator)
    - `async delete(key: str) -> None` (a missing object is not an error)
  - `get_storage() -> LocalStorage | S3Storage`, built from settings on every call. It is cheap, and tests monkeypatch the settings.

- [ ] **Step 1: Pin the dependency.**
  1. Append `pyzipper` to `backend/requirements.txt`.
  2. Rebuild with `docker compose build backend worker && docker compose up -d --no-deps backend worker`.
  3. Read the installed version with `docker compose exec -T backend pip show pyzipper | grep Version` and pin it exactly (`pyzipper==X.Y.Z`).
  4. Rebuild again.
  5. Run `docker compose exec -T backend sh -c 'pip install -q pip-audit && pip-audit -r requirements.txt'`. Expected output: `No known vulnerabilities found`.

- [ ] **Step 2: Settings.** In `backend/app/core/config.py`, add these next to the `AI_*` settings. `Literal` and `Field` are already imported there; check before adding.

```python
    STORAGE_BACKEND: Literal["local", "s3"] = "local"
    STORAGE_LOCAL_PATH: str = "/data/attachments"
    S3_BUCKET: Optional[str] = None
    S3_REGION: Optional[str] = None
    S3_ENDPOINT_URL: Optional[str] = None
    S3_PREFIX: str = ""
    MAX_UPLOAD_MB: int = Field(100, ge=1, le=5120)
```

Then add a model validator. Follow how the file already declares `@model_validator(mode="after")`; if one already exists, extend it:

```python
        if self.STORAGE_BACKEND == "s3" and not self.S3_BUCKET:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
```

- [ ] **Step 3: Write the failing tests** in `backend/tests/test_storage.py`:

```python
import os

import pytest

from app.storage import StorageError, get_storage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


async def agen(*parts):
    for p in parts:
        yield p


async def collect(it):
    return b"".join([c async for c in it])


@pytest.mark.asyncio
async def test_local_round_trip_and_delete(tmp_path):
    s = LocalStorage(str(tmp_path))
    n = await s.put("tenants/1/cases/2/abc", agen(b"hello ", b"world"))
    assert n == 11 and await collect(s.open("tenants/1/cases/2/abc")) == b"hello world"
    assert oct(os.stat(tmp_path / "tenants/1/cases/2").st_mode & 0o777) == "0o700"
    await s.delete("tenants/1/cases/2/abc")
    await s.delete("tenants/1/cases/2/abc")  # missing is fine
    assert not (tmp_path / "tenants/1/cases/2/abc").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["../x", "/etc/passwd", "a/../../x"])
async def test_local_rejects_escape(tmp_path, key):
    with pytest.raises(StorageError):
        await LocalStorage(str(tmp_path)).put(key, agen(b"x"))


@pytest.mark.asyncio
async def test_local_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"outside-{os.getpid()}"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside)
    try:
        with pytest.raises(StorageError):
            await LocalStorage(str(tmp_path)).put("link/x", agen(b"x"))
    finally:
        (tmp_path / "link").unlink()
        outside.rmdir()


@pytest.mark.asyncio
async def test_local_partial_never_visible(tmp_path):
    async def boom():
        yield b"part"
        raise RuntimeError("client went away")
    s = LocalStorage(str(tmp_path))
    with pytest.raises(RuntimeError):
        await s.put("k/obj", boom())
    assert not (tmp_path / "k/obj").exists() and not (tmp_path / "k/obj.part").exists()


class FakeBody:
    def __init__(self, data):
        self.data = data

    def iter_chunks(self, size):
        for i in range(0, len(self.data), size):
            yield self.data[i:i + size]


class FakeS3:
    def __init__(self):
        self.objects, self.calls = {}, []

    def upload_fileobj(self, f, bucket, key, ExtraArgs=None):
        self.calls.append(("put", bucket, key, ExtraArgs))
        self.objects[key] = f.read()

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete", Bucket, Key))
        self.objects.pop(Key, None)


@pytest.mark.asyncio
async def test_s3_put_open_delete(monkeypatch):
    fake, seen = FakeS3(), {}

    class Sess:
        def client(self, name, region_name=None, endpoint_url=None):
            seen.update(name=name, region=region_name, endpoint=endpoint_url)
            return fake
    monkeypatch.setattr("app.storage.s3.boto3.session.Session", lambda: Sess())
    s = S3Storage("bkt", region="eu-west-1", endpoint_url="http://minio:9000", prefix="soc/")
    assert await s.put("tenants/1/x", agen(b"ab", b"cd")) == 4
    assert fake.calls[0] == ("put", "bkt", "soc/tenants/1/x", {"ServerSideEncryption": "AES256"})
    assert seen == {"name": "s3", "region": "eu-west-1", "endpoint": "http://minio:9000"}
    assert await collect(s.open("tenants/1/x")) == b"abcd"
    await s.delete("tenants/1/x")
    assert fake.calls[-1] == ("delete", "bkt", "soc/tenants/1/x")


def test_get_storage_selects_backend(monkeypatch, tmp_path):
    from app.core.config import settings
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    assert isinstance(get_storage(), LocalStorage)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "b")
    monkeypatch.setattr("app.storage.s3.boto3.session.Session", lambda: type("S", (), {"client": lambda *a, **k: FakeS3()})())
    assert isinstance(get_storage(), S3Storage)
```

- [ ] **Step 4: Run the tests.** Expect a FAIL with `ModuleNotFoundError: app.storage`.

- [ ] **Step 5: Implement.**

`backend/app/storage/__init__.py`:

```python
"""Evidence file storage: local disk or S3-compatible, chosen by settings."""
from app.core.config import settings


class StorageError(Exception):
    pass


def get_storage():
    if settings.STORAGE_BACKEND == "s3":
        from app.storage.s3 import S3Storage
        return S3Storage(settings.S3_BUCKET, region=settings.S3_REGION,
                         endpoint_url=settings.S3_ENDPOINT_URL, prefix=settings.S3_PREFIX)
    from app.storage.local import LocalStorage
    return LocalStorage(settings.STORAGE_LOCAL_PATH)
```

`backend/app/storage/local.py`:

```python
import asyncio
import os
from pathlib import Path

from app.storage import StorageError

CHUNK = 65536


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise StorageError("invalid storage key")
        root = self.root.resolve()
        p = (root / key).resolve()
        if root != p and root not in p.parents:
            raise StorageError("invalid storage key")
        return p

    async def put(self, key, chunks) -> int:
        path = self._path(key)
        await asyncio.to_thread(os.makedirs, path.parent, 0o700, True)
        self._path(key)  # re-check after mkdir: a symlinked parent must still resolve inside root
        part = path.with_name(path.name + ".part")
        f = await asyncio.to_thread(open, part, "wb")
        n = 0
        try:
            async for c in chunks:
                await asyncio.to_thread(f.write, c)
                n += len(c)
            await asyncio.to_thread(f.flush)
            await asyncio.to_thread(os.fsync, f.fileno())
        except BaseException:
            f.close()
            await asyncio.to_thread(lambda: part.unlink(missing_ok=True))
            raise
        f.close()
        await asyncio.to_thread(os.replace, part, path)
        return n

    async def open(self, key):
        path = self._path(key)
        f = await asyncio.to_thread(open, path, "rb")
        try:
            while True:
                c = await asyncio.to_thread(f.read, CHUNK)
                if not c:
                    break
                yield c
        finally:
            f.close()

    async def delete(self, key) -> None:
        path = self._path(key)
        await asyncio.to_thread(lambda: path.unlink(missing_ok=True))
```

`os.makedirs(path, mode, exist_ok)` sets the mode only on directories it creates, and it is subject to the umask. If `test_local_round_trip_and_delete` sees something other than `0o700`, apply an explicit `os.chmod(d, 0o700)` to each directory created under the root.

`backend/app/storage/s3.py`:

```python
import asyncio
import tempfile

import boto3

CHUNK = 65536


class S3Storage:
    def __init__(self, bucket, region=None, endpoint_url=None, prefix=""):
        self.bucket, self.prefix = bucket, prefix or ""
        # Fresh session per instance: the default session is not thread-safe and calls run in to_thread.
        self.client = boto3.session.Session().client("s3", region_name=region, endpoint_url=endpoint_url)

    def _key(self, key):
        return f"{self.prefix}{key}"

    async def put(self, key, chunks) -> int:
        n = 0
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp:
            async for c in chunks:
                tmp.write(c)
                n += len(c)
            tmp.seek(0)
            await asyncio.to_thread(self.client.upload_fileobj, tmp, self.bucket, self._key(key),
                                    ExtraArgs={"ServerSideEncryption": "AES256"})
        return n

    async def open(self, key):
        body = (await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=self._key(key)))["Body"]
        it = body.iter_chunks(CHUNK)
        while True:
            c = await asyncio.to_thread(next, it, None)
            if c is None:
                break
            yield c

    async def delete(self, key) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))
```

- [ ] **Step 6: Run the tests.** Expect PASS. Then run the full suite.

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/app/core/config.py backend/app/storage backend/tests/test_storage.py
git commit -m "feat(evidence): storage backends (local, S3) and settings"
```

---

### Task 2: Model, migration and upload service

**Files:**
- Create: `backend/app/models/case_attachment.py`, `backend/alembic/versions/a5b6c7d8e9f0_case_attachments.py`, `backend/app/services/attachments.py`
- Modify: `backend/app/db/base.py`. Register the model the same way `tenant_enrichment_config` is registered.
- Test: `backend/tests/test_attachments_service.py`

**Interfaces:**
- Consumes:
  - Task 1: `get_storage()`
  - `app.services.case_service.add_artifact_to_case(db, *, case, artifact_type, value, description, user_id)`
  - `app.models.artifact.ArtifactType.FILE_HASH`
  - `app.models.case.TimelineEvent(case_id, user_id, event_type, content)`
  - `app.utils.audit.create_audit_log`
- Produces:
  - Model `CaseAttachment`, with the columns from the spec.
  - `sanitize_filename(name: str) -> str`
  - `class TooLarge(Exception)` and `class EmptyFile(Exception)`
  - `async def store_upload(db, *, case, user_id: Optional[int], filename: str, content_type: Optional[str], chunks: AsyncIterator[bytes], is_malicious: bool, description: Optional[str], storage=None, max_bytes: Optional[int] = None) -> CaseAttachment`
    - On success it commits.
    - On `TooLarge` or `EmptyFile` it removes any stored object and writes no row.
    - If the commit fails it deletes the stored object, then re-raises.
  - `def build_encrypted_zip(member_name: str, src) -> SpooledTemporaryFile`

- [ ] **Step 1: Model and migration.**

`backend/app/models/case_attachment.py`:

```python
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.db.base_class import Base


class CaseAttachment(Base):
    __tablename__ = "case_attachments"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(255), nullable=True)
    size_bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    is_malicious = Column(Boolean, nullable=False, server_default="false", default=False)
    storage_key = Column(String, nullable=False, unique=True)
    description = Column(String(1000), nullable=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
```

The migration `a5b6c7d8e9f0_case_attachments.py` (revision `a5b6c7d8e9f0`, `down_revision = "f4a5b6c7d8e9"`):
- Creates the same table, with `sa.UniqueConstraint("storage_key")`.
- Creates indexes `ix_case_attachments_id`, `ix_case_attachments_tenant_id`, `ix_case_attachments_case_id` and `ix_case_attachments_sha256`.
- `downgrade()` drops the table.

Run `alembic upgrade head`, then `downgrade -1`, then `upgrade head`. Expected: all succeed, and `alembic heads` shows `a5b6c7d8e9f0 (head)`.

- [ ] **Step 2: Write the failing tests** in `backend/tests/test_attachments_service.py`:

```python
import hashlib
import io
import secrets

import pytest
import pyzipper
from sqlalchemy import delete, func, select

from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_attachment import CaseAttachment
from app.models.tenant import Tenant
from app.services import attachments as svc
from app.storage.local import LocalStorage


@pytest.mark.parametrize("raw,want", [
    ("../../etc/passwd", "passwd"), ("C:\\x\\y.exe", "y.exe"), ("a\r\nb.txt", "ab.txt"),
    ("bad\x00name.pdf", "badname.pdf"), ("résumé 📎.pdf", "résumé 📎.pdf"), ("", "attachment"),
    ("..", "attachment"), (".", "attachment"), ("   ", "attachment"),
])
def test_sanitize_filename(raw, want):
    assert svc.sanitize_filename(raw) == want


def test_sanitize_trims_to_255_utf8_bytes():
    out = svc.sanitize_filename("é" * 300 + ".txt")
    assert len(out.encode()) <= 255 and out.encode().decode() == out


def test_encrypted_zip_round_trip():
    src = io.BytesIO(b"MZ\x90 evil")
    z = svc.build_encrypted_zip("evil.exe", src)
    with pyzipper.AESZipFile(z) as zf:
        zf.setpassword(b"infected")
        assert zf.namelist() == ["evil.exe"] and zf.read("evil.exe") == b"MZ\x90 evil"


async def agen(*parts):
    for p in parts:
        yield p


@pytest.fixture
async def ctx(tmp_path):
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ev")
        db.add(t)
        await db.flush()
        c = Case(title="ev", tenant_id=t.id)
        db.add(c)
        await db.commit()
        tid, cid = t.id, c.id
    yield {"tid": tid, "cid": cid, "storage": LocalStorage(str(tmp_path)), "root": tmp_path}
    async with AsyncSessionLocal() as db:
        await db.execute(delete(CaseAttachment).where(CaseAttachment.tenant_id == tid))
        await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == cid))
        await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == cid))
        await db.execute(delete(Artifact).where(Artifact.tenant_id == tid))
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
        await db.execute(delete(Case).where(Case.id == cid))
        await db.execute(delete(Tenant).where(Tenant.id == tid))
        await db.commit()
    await engine.dispose()


async def load_case(db, cid):
    return (await db.execute(select(Case).where(Case.id == cid))).scalars().one()


@pytest.mark.asyncio
async def test_store_upload_plain(ctx):
    data = b"x" * 70000
    async with AsyncSessionLocal() as db:
        a = await svc.store_upload(db, case=await load_case(db, ctx["cid"]), user_id=None, filename="../log.txt",
                                   content_type="text/plain", chunks=agen(data[:65536], data[65536:]),
                                   is_malicious=False, description="d", storage=ctx["storage"], max_bytes=10**6)
        assert a.filename == "log.txt" and a.size_bytes == 70000 and a.sha256 == hashlib.sha256(data).hexdigest()
        assert a.storage_key.startswith(f"tenants/{ctx['tid']}/cases/{ctx['cid']}/")
        got = b"".join([c async for c in ctx["storage"].open(a.storage_key)])
        assert got == data
        n_art = (await db.execute(select(func.count()).select_from(Artifact).where(
            Artifact.tenant_id == ctx["tid"], Artifact.value == a.sha256))).scalar()
        assert n_art == 1
        ev = (await db.execute(select(TimelineEvent).where(TimelineEvent.case_id == ctx["cid"],
                                                           TimelineEvent.event_type == "attachment_added"))).scalars().all()
        assert len(ev) == 1 and ev[0].content == f"Attached log.txt (sha256 {a.sha256[:12]}…)"
        audit = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == ctx["tid"],
                                                          AuditLog.entity_type == "attachment"))).scalars().all()
        assert [x.action for x in audit] == ["upload"]
        # same bytes again -> second attachment, still one artifact
        b = await svc.store_upload(db, case=await load_case(db, ctx["cid"]), user_id=None, filename="copy.txt",
                                   content_type=None, chunks=agen(data), is_malicious=False, description=None,
                                   storage=ctx["storage"], max_bytes=10**6)
        assert b.id != a.id and b.sha256 == a.sha256
        assert (await db.execute(select(func.count()).select_from(Artifact).where(
            Artifact.tenant_id == ctx["tid"], Artifact.value == a.sha256))).scalar() == 1


@pytest.mark.asyncio
async def test_store_upload_malicious_zip(ctx):
    data = b"MZ evil payload"
    async with AsyncSessionLocal() as db:
        a = await svc.store_upload(db, case=await load_case(db, ctx["cid"]), user_id=None, filename="evil.exe",
                                   content_type=None, chunks=agen(data), is_malicious=True, description=None,
                                   storage=ctx["storage"], max_bytes=10**6)
    assert a.is_malicious and a.sha256 == hashlib.sha256(data).hexdigest() and a.size_bytes == len(data)
    stored = b"".join([c async for c in ctx["storage"].open(a.storage_key)])
    assert stored[:2] == b"PK"
    with pyzipper.AESZipFile(io.BytesIO(stored)) as zf:
        zf.setpassword(b"infected")
        assert zf.read("evil.exe") == data


@pytest.mark.asyncio
@pytest.mark.parametrize("malicious", [False, True])
async def test_too_large_and_empty_leave_nothing(ctx, malicious):
    async with AsyncSessionLocal() as db:
        case = await load_case(db, ctx["cid"])
        with pytest.raises(svc.TooLarge):
            await svc.store_upload(db, case=case, user_id=None, filename="big.bin", content_type=None,
                                   chunks=agen(b"a" * 10, b"b" * 11), is_malicious=malicious, description=None,
                                   storage=ctx["storage"], max_bytes=20)
        with pytest.raises(svc.EmptyFile):
            await svc.store_upload(db, case=case, user_id=None, filename="e.bin", content_type=None,
                                   chunks=agen(), is_malicious=malicious, description=None,
                                   storage=ctx["storage"], max_bytes=20)
        n = (await db.execute(select(func.count()).select_from(CaseAttachment).where(
            CaseAttachment.tenant_id == ctx["tid"]))).scalar()
    assert n == 0
    assert [p for p in ctx["root"].rglob("*") if p.is_file()] == []


@pytest.mark.asyncio
async def test_commit_failure_deletes_object(ctx, monkeypatch):
    deleted = []

    class Spy(LocalStorage):
        async def delete(self, key):
            deleted.append(key)
            await super().delete(key)
    storage = Spy(str(ctx["root"]))
    async with AsyncSessionLocal() as db:
        case = await load_case(db, ctx["cid"])

        async def boom():
            raise RuntimeError("db down")
        monkeypatch.setattr(db, "commit", boom)
        with pytest.raises(RuntimeError):
            await svc.store_upload(db, case=case, user_id=None, filename="a.txt", content_type=None,
                                   chunks=agen(b"abc"), is_malicious=False, description=None,
                                   storage=storage, max_bytes=100)
        await db.rollback()
    assert len(deleted) == 1 and [p for p in ctx["root"].rglob("*") if p.is_file()] == []
```

- [ ] **Step 3: Run the tests.** Expect FAIL.

- [ ] **Step 4: Implement** `backend/app/services/attachments.py`:

```python
"""Evidence upload pipeline: stream -> hash/limit -> (zip if malicious) -> store -> rows."""
import hashlib
import logging
import re
import tempfile
import uuid
from typing import AsyncIterator, Optional

import pyzipper

from app.core.config import settings
from app.models.artifact import ArtifactType
from app.models.case import TimelineEvent
from app.models.case_attachment import CaseAttachment
from app.services.case_service import add_artifact_to_case
from app.storage import get_storage
from app.utils.audit import create_audit_log

logger = logging.getLogger(__name__)
CHUNK = 65536
ZIP_PASSWORD = b"infected"
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class TooLarge(Exception):
    pass


class EmptyFile(Exception):
    pass


def sanitize_filename(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1]
    base = _CTRL.sub("", base).strip()
    if base.strip(".") == "":
        return "attachment"
    raw = base.encode("utf-8")[:255]
    return raw.decode("utf-8", errors="ignore") or "attachment"


def build_encrypted_zip(member_name: str, src):
    out = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    src.seek(0)
    with pyzipper.AESZipFile(out, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(ZIP_PASSWORD)
        with zf.open(member_name, "w") as dst:  # streams; never loads the whole file into memory
            while True:
                c = src.read(CHUNK)
                if not c:
                    break
                dst.write(c)
    out.seek(0)
    return out


async def _file_chunks(f):
    while True:
        c = f.read(CHUNK)
        if not c:
            break
        yield c


async def store_upload(db, *, case, user_id, filename, content_type, chunks: AsyncIterator[bytes], is_malicious,
                       description, storage=None, max_bytes: Optional[int] = None) -> CaseAttachment:
    storage = storage or get_storage()
    max_bytes = max_bytes if max_bytes is not None else settings.MAX_UPLOAD_MB * 1024 * 1024
    name = sanitize_filename(filename)
    key = f"tenants/{case.tenant_id}/cases/{case.id}/{uuid.uuid4().hex}"
    h, size = hashlib.sha256(), 0

    async def counted():
        nonlocal size
        async for c in chunks:
            size += len(c)
            if size > max_bytes:
                raise TooLarge()
            h.update(c)
            yield c

    try:
        if is_malicious:
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as raw:
                async for c in counted():
                    raw.write(c)
                if size == 0:
                    raise EmptyFile()
                with build_encrypted_zip(name, raw) as z:
                    await storage.put(key, _file_chunks(z))
        else:
            await storage.put(key, counted())
            if size == 0:
                raise EmptyFile()
        row = CaseAttachment(tenant_id=case.tenant_id, case_id=case.id, filename=name,
                             content_type=(content_type or None) and content_type[:255], size_bytes=size,
                             sha256=h.hexdigest(), is_malicious=bool(is_malicious), storage_key=key,
                             description=description, uploaded_by=user_id)
        db.add(row)
        await db.flush()
        await add_artifact_to_case(db, case=case, artifact_type=ArtifactType.FILE_HASH, value=row.sha256,
                                   description=f"SHA-256 of attachment {name}", user_id=user_id)
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="attachment_added",
                             content=f"Attached {name} (sha256 {row.sha256[:12]}…)"))
        await create_audit_log(db=db, entity_type="attachment", entity_id=row.id, action="upload",
                               tenant_id=case.tenant_id, user_id=user_id,
                               changes={"filename": name, "sha256": row.sha256, "is_malicious": row.is_malicious,
                                        "case_id": case.id})
        await db.commit()
        await db.refresh(row)
        return row
    except BaseException:
        # Best effort: put may have written (part of) the object before the failure, including cancellation.
        try:
            await storage.delete(key)
        except Exception:
            logger.warning("attachment cleanup failed tenant=%s case=%s", case.tenant_id, case.id)
        raise
```

> Check `add_artifact_to_case` first. Its signature has `isolated` and `description` arguments, and it flushes but does not commit. Pass the arguments by keyword exactly as above.

- [ ] **Step 5: Run the tests,** then the full suite. Expect PASS, with a `wf-test-%` count of 0.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/case_attachment.py backend/app/db/base.py backend/alembic/versions/a5b6c7d8e9f0_case_attachments.py \
  backend/app/services/attachments.py backend/tests/test_attachments_service.py
git commit -m "feat(evidence): case_attachments table and streaming upload pipeline"
```

---

### Task 3: Attachments API

**Files:**
- Create: `backend/app/schemas/attachment.py`, `backend/app/api/v1/attachments.py`
- Modify: `backend/app/api/api.py`
  - Import `attachments`.
  - Add `api_router.include_router(attachments.router, prefix="/cases", tags=["attachments"])`.
- Test: `backend/tests/test_attachments_api.py`

**Interfaces:**
- Consumes (from Task 2): `store_upload`, `TooLarge`, `EmptyFile`, `CaseAttachment`.
- Consumes (from Task 1): `get_storage`.
- Consumes (dependencies): `deps.get_effective_tenant_id`, `deps.get_current_active_user`, `deps.require_analyst_or_above`, `app.utils.roles.resolve_active_role`.
- Produces: the routes in the spec, at paths `/{case_id}/attachments`, `/{case_id}/attachments/{att_id}/download` and `/{case_id}/attachments/{att_id}`.
- `AttachmentOut` (pydantic, `from_attributes`) has these fields:

  | Field | Type |
  |---|---|
  | `id` | int |
  | `case_id` | int |
  | `filename` | str |
  | `content_type` | Optional[str] |
  | `size_bytes` | int |
  | `sha256` | str |
  | `is_malicious` | bool |
  | `description` | Optional[str] |
  | `uploaded_by` | Optional[int] |
  | `uploaded_by_email` | Optional[str] |
  | `created_at` | datetime |
  | `deleted_at` | Optional[datetime] |
  | `deleted_by` | Optional[int] |
  | `deleted_by_email` | Optional[str] |

  The emails are filled by joining `User`. There is no `storage_key` field.
- `AttachmentList`: `{max_upload_mb: int, items: list[AttachmentOut]}`.

- [ ] **Step 1: Write the failing tests** in `backend/tests/test_attachments_api.py`. Follow the scenario pattern in `backend/tests/test_ti_api.py`:
  - Two temp tenants, each with one case, plus a user in tenant A.
  - Overrides for `get_current_active_user`, `get_effective_tenant_id` and `require_analyst_or_above`. The last one raises 403 when the scenario role is `viewer`.
  - Monkeypatch `settings.STORAGE_LOCAL_PATH` to `tmp_path`, and `settings.MAX_UPLOAD_MB` to 1.
  - Use an in-process `httpx.AsyncClient`.
  - Clean up children first, by exact ids.

  Concrete assertions:

```python
# a) POST multipart file=("../e vil.txt", b"hello", "text/plain") -> 201; json filename "e vil.txt", size 5,
#    sha256 == sha256(b"hello"), is_malicious False, "storage_key" not in json
# b) GET list -> 200 {max_upload_mb: 1, items:[...]} newest first
# c) download -> 200, body b"hello", headers exactly:
#    content-type "application/octet-stream", x-content-type-options "nosniff",
#    content-security-policy "sandbox", cache-control "no-store", x-content-sha256 == sha,
#    content-disposition == "attachment; filename*=UTF-8''e%20vil.txt"
# d) POST with filename "a\r\nb.txt" -> content-disposition is one line, no CR/LF, filename*=UTF-8''ab.txt
# e) POST is_malicious=true file=("x.exe", b"MZ..") -> download filename*=UTF-8''x.exe.zip, body opens with pyzipper pw b"infected"
# f) POST 1 MiB + 1 bytes -> 413 detail "File exceeds 1 MB"; no new row; no files added under tmp_path
# g) POST empty file -> 422 detail "Empty file"
# h) role viewer: POST -> 403, DELETE -> 403 (and GET list/download still 200)
# i) tenant B context: GET list on A's case -> 404; download A's attachment via B's case id -> 404;
#    download A's attachment id under A's case while tenant is B -> 404
# j) DELETE -> 204; list omits it; download -> 404; DELETE again -> 404;
#    timeline has attachment_deleted; audit actions for that id == ["upload","download","delete"]
# k) include_deleted=true as analyst -> 403; as admin (resolve_active_role patched to "admin") -> row present with
#    deleted_by_email set
# l) logging: exactly one "attachment <op> tenant=... outcome=..." INFO line per upload/download/delete call,
#    and the file name never appears in any log record
```

  Every lettered check gets concrete asserts, each marked with its letter in a comment. To assert "no files added", compare `set(tmp_path.rglob("*"))` before and after the request.

- [ ] **Step 2: Run the tests.** Expect FAIL with 404 on the routes.

- [ ] **Step 3: Implement.** The structure of `backend/app/api/v1/attachments.py`:

```python
import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api import deps
from app.core.config import settings
from app.models.case import Case, TimelineEvent
from app.models.case_attachment import CaseAttachment
from app.models.user import User
from app.schemas.attachment import AttachmentList, AttachmentOut
from app.services.attachments import CHUNK, EmptyFile, TooLarge, store_upload
from app.storage import get_storage
from app.utils.audit import create_audit_log
from app.utils.roles import resolve_active_role

router = APIRouter()
logger = logging.getLogger(__name__)
_LOG = "attachment %s tenant=%s case=%s id=%s size=%s malicious=%s outcome=%s"


async def _case(db, case_id, tid) -> Case:
    c = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tid))).scalars().first()
    if c is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return c


async def _att(db, case_id, att_id, tid) -> CaseAttachment:
    a = (await db.execute(select(CaseAttachment).where(
        CaseAttachment.id == att_id, CaseAttachment.case_id == case_id, CaseAttachment.tenant_id == tid,
        CaseAttachment.deleted_at.is_(None)))).scalars().first()
    if a is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return a


async def _upload_chunks(f: UploadFile):
    while True:
        c = await f.read(CHUNK)
        if not c:
            break
        yield c
```

  The handlers:

  **`POST /{case_id}/attachments`** (status 201):
  - Parameters: `file: UploadFile = File(...)`, `is_malicious: bool = Form(False)`, `description: Optional[str] = Form(None, max_length=1000)`.
  - Call `store_upload(... chunks=_upload_chunks(file) ...)`.
  - Map `TooLarge` to 413 `f"File exceeds {settings.MAX_UPLOAD_MB} MB"`, and `EmptyFile` to 422 `"Empty file"`.
  - Log the line with outcome `ok`, `too_large` or `empty`.
  - Return `AttachmentOut`. Its emails come from one query joining `aliased(User)` twice.

  **`GET /{case_id}/attachments`**:
  - Takes `include_deleted: bool = Query(False)`. When it is True and the user is not `resolve_active_role(user) == "admin"` and not `user.is_super_admin`, return 403 `"Admins only"`.
  - Return `AttachmentList(max_upload_mb=settings.MAX_UPLOAD_MB, items=...)`, ordered by `created_at desc, id desc`.

  **`GET /{case_id}/attachments/{att_id}/download`**:
  - Load the attachment.
  - Write the audit entry (action `download`) and commit, before streaming.
  - Use `name = a.filename + (".zip" if a.is_malicious else "")`.
  - Return `StreamingResponse(get_storage().open(a.storage_key), media_type="application/octet-stream", headers={...})` with exactly these headers:
    - `Content-Disposition: attachment; filename*=UTF-8''{quote(name, safe='')}`
    - `X-Content-Type-Options: nosniff`
    - `Content-Security-Policy: sandbox`
    - `Cache-Control: no-store`
    - `X-Content-SHA256: <sha256>`
  - Log the line.

  **`DELETE /{case_id}/attachments/{att_id}`**:
  - Set `deleted_at = func.now()` and `deleted_by = user.id`.
  - Add a `TimelineEvent(event_type="attachment_deleted", content=f"Deleted attachment {a.filename}")`.
  - Write the audit entry (action `delete`), commit, and return `Response(status_code=204)`.
  - Log the line.

  > `resolve_active_role` already exists (`app/utils/roles.py`); check its signature (it may need `db` and `tenant_id`) and call it the way `deps.require_admin` does.

- [ ] **Step 4: Run the tests,** then the full suite. Expect PASS, with a `wf-test-%` count of 0. Restart the backend and the worker.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/attachment.py backend/app/api/v1/attachments.py backend/app/api/api.py backend/tests/test_attachments_api.py
git commit -m "feat(evidence): attachments API with safe downloads, soft delete and audit"
```

---

### Task 4: Infrastructure — compose volume, env and nginx upload limit

**Files:**
- Modify: `docker-compose.yml`, `frontend/Dockerfile`
- Move: `frontend/nginx.conf` → `frontend/nginx/default.conf.template`

- [ ] **Step 1: docker-compose.yml.**
  - Add `attachments_data:` under the top-level `volumes:`.
  - On both `backend` and `worker`, add the volume `- attachments_data:/data/attachments`.
  - On both, add these env lines:

    ```
    - STORAGE_BACKEND=${STORAGE_BACKEND:-local}
    - STORAGE_LOCAL_PATH=/data/attachments
    - MAX_UPLOAD_MB=${MAX_UPLOAD_MB:-100}
    - S3_BUCKET=${S3_BUCKET:-}
    - S3_REGION=${S3_REGION:-}
    - S3_ENDPOINT_URL=${S3_ENDPOINT_URL:-}
    - S3_PREFIX=${S3_PREFIX:-}
    ```

    Pydantic must treat the empty strings as None for the `Optional[str]` S3 settings. Check this. If `""` breaks the `STORAGE_BACKEND=s3` validation or the boto client, add a `field_validator(..., mode="before")` that maps `""` to None, the same way `AI_PROVIDER` does.
  - On `frontend`, add `environment: [MAX_UPLOAD_MB=${MAX_UPLOAD_MB:-100}]`.
  - The backend container runs as root, so `/data/attachments` is writable. Check this with `docker compose exec -T backend sh -c 'touch /data/attachments/.w && rm /data/attachments/.w'`.

- [ ] **Step 2: nginx template.**
  1. `git mv frontend/nginx.conf frontend/nginx/default.conf.template`.
  2. In its `location /api/v1/` block, add:

     ```
     client_max_body_size ${MAX_UPLOAD_MB}m;
     proxy_request_buffering off;
     proxy_read_timeout 300s;
     proxy_send_timeout 300s;
     ```

  3. In `frontend/Dockerfile`, replace `COPY nginx.conf /etc/nginx/conf.d/default.conf` with:

     ```
     COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
     ENV NGINX_ENVSUBST_FILTER=^MAX_UPLOAD_MB$
     ENV MAX_UPLOAD_MB=100
     ```

     The official nginx image renders `/etc/nginx/templates/*.template` into `conf.d` at start. With the filter set, only `MAX_UPLOAD_MB` is substituted, so `$host` and `$remote_addr` survive.

- [ ] **Step 3: Verify.**
  1. `docker compose build frontend backend worker && docker compose up -d --no-deps frontend backend worker`
  2. `docker compose exec -T frontend sh -c 'grep -n "client_max_body_size\|proxy_set_header Host" /etc/nginx/conf.d/default.conf'`. Expected output: `client_max_body_size 100m;` and `proxy_set_header Host $host;`, with the latter unsubstituted.
  3. `curl -s -o /dev/null -w "%{http_code}" http://localhost/` should print 200.
  4. Run actionlint, if available, on the repo: `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest -no-color`. The workflows are untouched, so this is only a sanity check.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml frontend/Dockerfile frontend/nginx
git commit -m "feat(evidence): attachments volume, storage env and nginx upload limit"
```

---

### Task 5: Frontend — Evidence tab

**Files:**
- Create: `frontend/src/features/cases/CaseEvidence.tsx`
- Modify: `frontend/src/features/cases/CaseDetail.tsx`, adding an `'evidence'` tab next to `'artifacts'` in the `activeTab` union and the tab list, and rendering `<CaseEvidence caseId={parseInt(id)} />`
- Modify: `frontend/src/types/index.ts`, adding `Attachment` and `AttachmentList`, which mirror `AttachmentOut` and `AttachmentList` in `backend/app/schemas/attachment.py`

**Behaviour.** Read `backend/app/api/v1/attachments.py` for the exact shapes.

- **Data:** `useQuery(['attachments', caseId, showDeleted], GET /cases/{caseId}/attachments?include_deleted=…)`.
- **Who can do what:**
  - `canWrite` (upload, delete): the same rule as `useCanRun` in `frontend/src/features/enrichment/useCanRun.ts`, i.e. admin, analyst or super admin. Reuse that hook.
  - `isAdmin`: `role === 'admin' || is_super_admin`. Only admins see the "Show deleted" toggle.
- **Upload zone:** shown only when `canWrite`.
  - A drop area plus a file input.
  - A "Malicious sample" checkbox with the hint "Stored and downloaded as a ZIP, password `infected`".
  - An optional description input with `maxLength` 1000.
  - Before uploading, if `file.size > max_upload_mb*1024*1024`, show "File exceeds {max_upload_mb} MB" and don't send.
  - Upload with `api.post(url, formData, { onUploadProgress })`, showing a percentage progress bar.
  - On error, show the response `detail`, which is a string for 413 and 422.
  - On success, invalidate `['attachments', caseId]` and `['artifacts', String(caseId)]`. The latter is CaseDetail's artifacts query key; check it, because the hash artifact appears there.
- **Table columns:**
  - name
  - human-readable size (B/KB/MB/GB)
  - SHA-256: the first 12 characters in `.num`, with a copy button that uses `navigator.clipboard.writeText` and catches failures
  - uploaded by (email) and relative time
  - a red "malicious" badge when flagged
  - a Download button: "Download (ZIP, pw: infected)" for flagged files
  - Delete, when `canWrite`, with an inline confirm row (no `window.confirm`)
- **Deleted rows:** when "Show deleted" is on, deleted rows appear greyed out with "deleted by {email}", and have no buttons.
- **Download:**
  - `api.get(url, { responseType: 'blob' })`.
  - Parse the `filename*=UTF-8''…` part of `content-disposition` with `decodeURIComponent`. Fall back to the row's filename plus `.zip` when flagged.
  - Create an object URL, click a temporary `<a download>`, then revoke the URL.
  - Never use `window.open`.
- **Theme:** light Telemetry Console. Match the panel and table classes used by the Artifacts tab in `CaseDetail.tsx`.

- [ ] **Step 1:** Add the types.
- [ ] **Step 2:** Write `CaseEvidence.tsx` to the behaviour above.
- [ ] **Step 3:** Wire it into `CaseDetail.tsx`.
- [ ] **Step 4: Verify.**
  1. `cd frontend && npm run build`.
  2. `npx eslint src/features/cases/CaseEvidence.tsx`.
  3. CaseDetail's existing `no-explicit-any` errors must not increase.
  4. `docker compose up -d --no-deps --build frontend`.
  5. `curl -s -o /dev/null -w "%{http_code}" http://localhost/` should print 200.
- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(evidence): Evidence tab with upload, safe download and soft delete"
```

---

### Task 6: Docs and end-to-end smoke

**Files:** modify `docs/configuration.md` and `docs/features.md`.

- [ ] **Step 1: Docs.** Add an "Evidence attachments" section to `docs/configuration.md` covering:
  - The storage settings table (`STORAGE_BACKEND`, `STORAGE_LOCAL_PATH`, `S3_*`, `MAX_UPLOAD_MB`), with defaults.
  - The `attachments_data` volume, and that it must be backed up.
  - S3 credentials come from the standard AWS chain, IAM role first.
  - Uploads use server-side encryption, `AES256`.
  - The nginx limit follows `MAX_UPLOAD_MB`.
  - Malicious samples are stored as an AES ZIP, password `infected`.
  - The download safety headers.
  - Soft delete: objects are kept, and only admins can list deleted attachments.
  - Each upload adds a SHA-256 `file_hash` artifact, which follows the threat-intel TLP rules.
  - What is logged: no file names, no contents.

  Add a short "Evidence attachments" entry to `docs/features.md`.

- [ ] **Step 2: In-container smoke test.** No network, and a temporary storage path.
  1. Create a temp tenant (`wf-test-<hex>-ev`), a case and a user.
  2. With `settings.STORAGE_LOCAL_PATH` set to a temp dir, use dependency overrides and an in-process client to:
     - POST a 3-byte file and a malicious file;
     - list them;
     - download both, and check the ZIP opens with `infected`;
     - DELETE one.
  3. Print the statuses and hashes.
  4. Delete every row by id, and remove the temp dir.
  5. Confirm the `wf-test-%` count is 0.
  6. Record the output in the report.
- [ ] **Step 3: Commit**

```bash
git add docs/configuration.md docs/features.md
git commit -m "docs(evidence): evidence attachments configuration and feature notes"
```
