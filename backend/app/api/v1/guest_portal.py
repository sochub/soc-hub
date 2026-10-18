"""Guest portal API: one case at a time, only what the grant allows. Errors never reveal other cases."""
import logging
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.api.v1.attachments import _upload_chunks
from app.core.config import settings
from app.core.login_throttle import get_login_throttle
from app.guests.access import GuestSession, get_current_guest, get_guest_grant, require_contributor, visible_grants
from app.models.case import Case, TimelineEvent
from app.models.case_attachment import CaseAttachment
from app.models.case_guest import CaseGuest, GuestAccount
from app.notifications import service as notify
from app.services.attachments import EmptyFile, TooLarge, store_upload
from app.utils.audit import create_audit_log

router = APIRouter()
logger = logging.getLogger(__name__)


class GuestCaseItem(BaseModel):
    id: int
    title: str
    tenant_name: str
    permission: str


class GuestCaseOut(BaseModel):
    id: int
    title: str
    status: str
    severity: str
    guest_summary: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    permission: str


class GuestEventOut(BaseModel):
    id: int
    event_type: str
    content: str
    created_at: datetime
    author: str
    from_guest: bool


class CommentIn(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class GuestAttachmentOut(BaseModel):
    id: int
    filename: str
    size_bytes: int
    sha256: str
    created_at: datetime


UPLOAD_LIMIT = 50                 # uploads per grant ...
UPLOAD_WINDOW_SECONDS = 24 * 3600  # ... per fixed 24h window (from the first upload)


def get_upload_redis():
    """Same lazy Redis client as the login throttle; tests override it."""
    return get_login_throttle().redis


async def _reserve_upload(redis, grant_id: int) -> None:
    """Fixed-window counter on gup:{grant}. Fails open like the login throttle: a Redis outage allows uploads."""
    key = f"gup:{grant_id}"
    try:
        n = await redis.incr(key)
        # EXPIRE NX (Redis 7+): the window starts at the first upload, and a key can never be left without a TTL
        await redis.expire(key, UPLOAD_WINDOW_SECONDS, nx=True)
    except Exception as exc:
        logger.warning("guest upload limiter unavailable: %s", type(exc).__name__)
        return
    if n > UPLOAD_LIMIT:
        raise HTTPException(status_code=429, detail="Upload limit reached. Try again later.")


def _v(x) -> str:
    return getattr(x, "value", x)


def _event_out(ev: TimelineEvent, grant_id: int) -> GuestEventOut:
    if ev.guest_id is not None:
        author = "You"  # other guests' events are filtered out of the timeline
    elif ev.user is not None:
        author = ev.user.full_name or ev.user.email.split("@")[0]
    else:
        author = "SOC team"
    return GuestEventOut(id=ev.id, event_type=ev.event_type or "other", content=ev.content or "",
                         created_at=ev.created_at, author=author, from_guest=ev.guest_id is not None)


async def _case(db: AsyncSession, grant: CaseGuest) -> Case:
    return (await db.execute(select(Case).where(Case.id == grant.case_id, Case.tenant_id == grant.tenant_id))
            ).scalars().one()


@router.get("/me")
async def me(db: AsyncSession = Depends(deps.get_db), s: GuestSession = Depends(get_current_guest)) -> Any:
    acct = (await db.execute(select(GuestAccount).where(GuestAccount.email == s.email))).scalars().first()
    return {"email": s.email, "has_account": acct is not None,
            "mfa_enabled": bool(acct and acct.mfa_enabled_at), "amr": s.amr}


@router.get("/cases", response_model=List[GuestCaseItem])
async def my_cases(db: AsyncSession = Depends(deps.get_db), s: GuestSession = Depends(get_current_guest)) -> Any:
    grants = await visible_grants(db, s)
    if not grants:
        return []
    titles = dict((await db.execute(select(Case.id, Case.title).where(
        Case.id.in_([g.case_id for g, _ in grants])))).all())
    return [GuestCaseItem(id=g.case_id, title=titles.get(g.case_id, ""), tenant_name=t.name, permission=g.permission)
            for g, t in grants]


@router.get("/cases/{case_id}", response_model=GuestCaseOut)
async def guest_case(case_id: int, db: AsyncSession = Depends(deps.get_db),
                     grant: CaseGuest = Depends(get_guest_grant)) -> Any:
    c = await _case(db, grant)
    return GuestCaseOut(id=c.id, title=c.title, status=_v(c.status), severity=_v(c.severity),
                        guest_summary=c.guest_summary, created_at=c.created_at, updated_at=c.updated_at,
                        permission=grant.permission)


@router.get("/cases/{case_id}/timeline", response_model=List[GuestEventOut])
async def guest_timeline(case_id: int, db: AsyncSession = Depends(deps.get_db),
                         grant: CaseGuest = Depends(get_guest_grant)) -> Any:
    rows = (await db.execute(
        select(TimelineEvent).options(selectinload(TimelineEvent.user))
        .where(TimelineEvent.case_id == grant.case_id, TimelineEvent.shared_with_guests.is_(True),
                (TimelineEvent.guest_id.is_(None)) | (TimelineEvent.guest_id == grant.id))
        .order_by(TimelineEvent.created_at, TimelineEvent.id)
    )).scalars().unique().all()
    return [_event_out(e, grant.id) for e in rows]


@router.post("/cases/{case_id}/comments", response_model=GuestEventOut, status_code=201)
async def guest_comment(case_id: int, body: CommentIn, db: AsyncSession = Depends(deps.get_db),
                        grant: CaseGuest = Depends(require_contributor)) -> Any:
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Comment is empty")
    case = await _case(db, grant)
    ev = TimelineEvent(case_id=case.id, user_id=None, guest_id=grant.id, event_type="guest_comment",
                       content=content, shared_with_guests=True)
    db.add(ev)
    await db.flush()
    await create_audit_log(db=db, entity_type="timeline_event", entity_id=ev.id, action="guest_comment",
                           tenant_id=case.tenant_id, changes={"actor": f"guest:{grant.email}", "case_id": case.id})
    await notify.on_comment(db, case, ev, None, actor_label=grant.email)
    await db.commit()
    ev = (await db.execute(select(TimelineEvent).options(selectinload(TimelineEvent.user))
                           .where(TimelineEvent.id == ev.id))).scalars().one()
    return _event_out(ev, grant.id)


@router.get("/cases/{case_id}/attachments", response_model=List[GuestAttachmentOut])
async def guest_attachments(case_id: int, db: AsyncSession = Depends(deps.get_db),
                            grant: CaseGuest = Depends(get_guest_grant)) -> Any:
    rows = (await db.execute(select(CaseAttachment).where(
        CaseAttachment.case_id == grant.case_id, CaseAttachment.uploaded_by_guest_id == grant.id,
        CaseAttachment.deleted_at.is_(None)).order_by(CaseAttachment.created_at.desc()))).scalars().all()
    return [GuestAttachmentOut(id=a.id, filename=a.filename, size_bytes=a.size_bytes, sha256=a.sha256,
                               created_at=a.created_at) for a in rows]


@router.post("/cases/{case_id}/attachments", response_model=GuestAttachmentOut, status_code=201)
async def guest_upload(case_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(deps.get_db),
                       grant: CaseGuest = Depends(require_contributor), redis=Depends(get_upload_redis)) -> Any:
    await _reserve_upload(redis, grant.id)
    case = await _case(db, grant)
    # store_upload may roll back and expire ORM objects
    grant_id, tenant_id, actor = grant.id, grant.tenant_id, f"guest:{grant.email}"
    try:
        row = await store_upload(db, case=case, user_id=None, filename=file.filename or "",
                                 content_type=file.content_type, chunks=_upload_chunks(file),
                                 is_malicious=False, description=None, guest_id=grant_id, actor=actor)
    except TooLarge:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.MAX_UPLOAD_MB} MB")
    except EmptyFile:
        raise HTTPException(status_code=422, detail="Empty file")
    logger.info("guest attachment upload tenant=%s case=%s id=%s outcome=ok", tenant_id, case_id, row.id)
    return GuestAttachmentOut(id=row.id, filename=row.filename, size_bytes=row.size_bytes, sha256=row.sha256,
                              created_at=row.created_at)
