import logging
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import func

from app.api import deps
from app.core.config import settings
from app.models.case import Case, TimelineEvent
from app.models.case_attachment import CaseAttachment
from app.models.user import User
from app.schemas.attachment import AttachmentList, AttachmentOut
from app.services.attachments import CHUNK, EmptyFile, TooLarge, store_upload
from app.storage import StorageError, get_storage
from app.utils.audit import create_audit_log

router = APIRouter()
logger = logging.getLogger(__name__)
# Never log file names, contents or storage keys: ids, sizes, flags and outcomes only.
_LOG = "attachment %s tenant=%s case=%s id=%s size=%s malicious=%s outcome=%s"


async def _case(db: AsyncSession, case_id: int, tid: int) -> Case:
    c = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tid))).scalars().first()
    if c is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return c


async def _att(db: AsyncSession, case_id: int, att_id: int, tid: int) -> CaseAttachment:
    a = (await db.execute(select(CaseAttachment).where(
        CaseAttachment.id == att_id, CaseAttachment.case_id == case_id, CaseAttachment.tenant_id == tid,
        CaseAttachment.deleted_at.is_(None)))).scalars().first()
    if a is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return a


async def _found(db: AsyncSession, op: str, case_id: int, att_id: int, tid: int) -> CaseAttachment:
    try:
        await _case(db, case_id, tid)
        return await _att(db, case_id, att_id, tid)
    except HTTPException:
        logger.info(_LOG, op, tid, case_id, att_id, None, None, "not_found")
        raise


def _details(a: CaseAttachment, case_id: int) -> dict:
    return {"filename": a.filename, "sha256": a.sha256, "is_malicious": a.is_malicious, "case_id": case_id}


async def _chain(first: bytes, it):
    try:
        if first:
            yield first
        async for c in it:
            yield c
    finally:
        await it.aclose()


async def _upload_chunks(f: UploadFile):
    while True:
        c = await f.read(CHUNK)
        if not c:
            break
        yield c


async def _out(db: AsyncSession, *where) -> list[AttachmentOut]:
    up, dl = aliased(User), aliased(User)
    res = await db.execute(
        select(CaseAttachment, up.email, dl.email)
        .outerjoin(up, up.id == CaseAttachment.uploaded_by)
        .outerjoin(dl, dl.id == CaseAttachment.deleted_by)
        .where(*where)
        .order_by(CaseAttachment.created_at.desc(), CaseAttachment.id.desc()))
    return [AttachmentOut.model_validate(a).model_copy(update={"uploaded_by_email": ue, "deleted_by_email": de})
            for a, ue, de in res.all()]


@router.post("/{case_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    file: UploadFile = File(...),
    is_malicious: bool = Form(False),
    description: Optional[str] = Form(None, max_length=1000),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    case = await _case(db, case_id, tenant_id)
    # store_upload rolls the session back on failure, expiring every ORM instance: capture plain values first.
    user_id, malicious = current_user.id, bool(is_malicious)
    try:
        row = await store_upload(db, case=case, user_id=user_id, filename=file.filename or "",
                                 content_type=file.content_type, chunks=_upload_chunks(file),
                                 is_malicious=malicious, description=description)
    except TooLarge:
        logger.info(_LOG, "upload", tenant_id, case_id, None, None, malicious, "too_large")
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_UPLOAD_MB} MB")
    except EmptyFile:
        logger.info(_LOG, "upload", tenant_id, case_id, None, 0, malicious, "empty")
        raise HTTPException(status_code=422, detail="Empty file")
    except Exception:
        logger.info(_LOG, "upload", tenant_id, case_id, None, None, malicious, "error")
        raise
    att_id, size = row.id, row.size_bytes
    logger.info(_LOG, "upload", tenant_id, case_id, att_id, size, malicious, "ok")
    return (await _out(db, CaseAttachment.id == att_id))[0]


@router.get("/{case_id}/attachments", response_model=AttachmentList)
async def list_attachments(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    include_deleted: bool = Query(False),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _case(db, case_id, tenant_id)
    if include_deleted and deps._active_role(current_user) not in ("super_admin", "admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    where = [CaseAttachment.case_id == case_id, CaseAttachment.tenant_id == tenant_id]
    if not include_deleted:
        where.append(CaseAttachment.deleted_at.is_(None))
    return AttachmentList(max_upload_mb=settings.MAX_UPLOAD_MB, items=await _out(db, *where))


@router.get("/{case_id}/attachments/{att_id}/download")
async def download_attachment(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    att_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    a = await _found(db, "download", case_id, att_id, tenant_id)
    name = a.filename + (".zip" if a.is_malicious else "")
    key, sha, size, malicious = a.storage_key, a.sha256, a.size_bytes, a.is_malicious
    user_id = current_user.id
    details = _details(a, case_id)
    # Prime the stream first, so a missing object is a 500 with no (false) download audit row.
    it = None
    try:
        it = get_storage().open(key)
        first = await it.__anext__()
    except StopAsyncIteration:
        first = b""
    except (FileNotFoundError, StorageError):
        if it is not None:
            await it.aclose()
        logger.info(_LOG, "download", tenant_id, case_id, att_id, size, malicious, "missing")
        raise HTTPException(status_code=500, detail="Attachment content unavailable")
    except BaseException:
        if it is not None:
            await it.aclose()
        logger.info(_LOG, "download", tenant_id, case_id, att_id, size, malicious, "error")
        raise
    try:
        # Written before streaming: a download audit row means "download started".
        await create_audit_log(db=db, entity_type="attachment", entity_id=att_id, action="download",
                               tenant_id=tenant_id, user_id=user_id, changes=details)
        await db.commit()
    except BaseException:
        await it.aclose()
        raise
    logger.info(_LOG, "download", tenant_id, case_id, att_id, size, malicious, "ok")
    return StreamingResponse(_chain(first, it), media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "no-store",
        "X-Content-SHA256": sha,
    })


@router.delete("/{case_id}/attachments/{att_id}", status_code=204)
async def delete_attachment(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    att_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Response:
    a = await _found(db, "delete", case_id, att_id, tenant_id)
    user_id, size, malicious, filename = current_user.id, a.size_bytes, a.is_malicious, a.filename
    details = _details(a, case_id)
    # Atomic: of two concurrent deletes only one matches `deleted_at IS NULL`; the other gets a 404.
    res = await db.execute(
        update(CaseAttachment)
        .where(CaseAttachment.id == att_id, CaseAttachment.tenant_id == tenant_id,
               CaseAttachment.case_id == case_id, CaseAttachment.deleted_at.is_(None))
        .values(deleted_at=func.now(), deleted_by=user_id)
        .execution_options(synchronize_session=False))
    if res.rowcount == 0:
        await db.rollback()
        logger.info(_LOG, "delete", tenant_id, case_id, att_id, None, None, "not_found")
        raise HTTPException(status_code=404, detail="Attachment not found")
    db.add(TimelineEvent(case_id=case_id, user_id=user_id, event_type="attachment_deleted",
                         content=f"Deleted attachment {filename}"))
    await create_audit_log(db=db, entity_type="attachment", entity_id=att_id, action="delete",
                           tenant_id=tenant_id, user_id=user_id, changes=details)
    await db.commit()
    logger.info(_LOG, "delete", tenant_id, case_id, att_id, size, malicious, "ok")
    return Response(status_code=204)
