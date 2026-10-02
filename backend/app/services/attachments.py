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
