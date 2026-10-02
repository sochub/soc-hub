import hashlib
import io
import secrets

import pytest
import pytest_asyncio
import pyzipper
from sqlalchemy import delete, func, select

import app.db.base  # noqa: F401  (registers all mappers)
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


@pytest_asyncio.fixture
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
