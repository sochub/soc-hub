import asyncio
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.tenant_mailbox import TenantMailbox
from app.models.user import User
from app.schemas.mailbox import MailboxIn, MailboxOut, MailboxUpdate, MailboxTestResult
from app.services import email_intake
from app.services.email_intake import validate_mailbox_host
from app.utils.audit import create_audit_log
from app.utils.crypto import encrypt

router = APIRouter()

_FIELDS = ("name", "host", "port", "use_ssl", "username", "folder", "poll_interval_seconds", "enabled",
           "fallback_create_alert")


async def _get(db: AsyncSession, tenant_id: int, mailbox_id: int) -> TenantMailbox:
    mb = (await db.execute(select(TenantMailbox).where(TenantMailbox.id == mailbox_id,
                                                       TenantMailbox.tenant_id == tenant_id))).scalars().first()
    if not mb:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    return mb


def _check_host(host: str) -> None:
    if (err := validate_mailbox_host(host)):
        raise HTTPException(status_code=422, detail=err)


def _out(mb: TenantMailbox) -> MailboxOut:
    out = MailboxOut.model_validate(mb)
    out.password_set = bool(mb.password_enc)
    return out


@router.get("/", response_model=List[MailboxOut])
async def list_mailboxes(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    rows = (await db.execute(select(TenantMailbox).where(TenantMailbox.tenant_id == tenant_id)
                             .order_by(TenantMailbox.id))).scalars().all()
    return [_out(m) for m in rows]


@router.post("/", response_model=MailboxOut, status_code=201)
async def create_mailbox(body: MailboxIn, db: AsyncSession = Depends(deps.get_db),
                         current_user: User = Depends(deps.require_admin),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    if not body.password:
        raise HTTPException(status_code=422, detail="A password is required")
    _check_host(body.host)
    mb = TenantMailbox(tenant_id=tenant_id, password_enc=encrypt(body.password),
                       **body.model_dump(include=set(_FIELDS)))
    db.add(mb)
    await db.flush()
    await create_audit_log(db=db, entity_type="mailbox", entity_id=mb.id, action="created", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"name": mb.name, "host": mb.host, "username": mb.username})
    await db.commit()
    await db.refresh(mb)
    return _out(mb)


@router.put("/{mailbox_id}", response_model=MailboxOut)
async def update_mailbox(mailbox_id: int, body: MailboxUpdate, db: AsyncSession = Depends(deps.get_db),
                         current_user: User = Depends(deps.require_admin),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    mb = await _get(db, tenant_id, mailbox_id)
    if body.host is not None:
        _check_host(body.host)
    target = (mb.host.lower(), mb.port, mb.username)
    # Partial update: only fields the client sent change.
    for k, v in body.model_dump(include=set(_FIELDS), exclude_unset=True).items():
        if v is not None:
            setattr(mb, k, v)
    # The stored password only carries over to the same server and account: otherwise an admin (or a hijacked
    # admin session) could point it at a host of their choosing and press Test.
    if not body.password and (mb.host.lower(), mb.port, mb.username) != target:
        raise HTTPException(status_code=422, detail="the password is required again when the host, port or username changes")
    if body.password:
        mb.password_enc = encrypt(body.password)
    await create_audit_log(db=db, entity_type="mailbox", entity_id=mb.id, action="updated", tenant_id=tenant_id,
                           user_id=current_user.id,
                           changes={"name": mb.name, "host": mb.host, "username": mb.username,
                                    "enabled": mb.enabled, "password_changed": bool(body.password)})
    await db.commit()
    await db.refresh(mb)
    return _out(mb)


@router.delete("/{mailbox_id}", status_code=204)
async def delete_mailbox(mailbox_id: int, db: AsyncSession = Depends(deps.get_db),
                         current_user: User = Depends(deps.require_admin),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    mb = await _get(db, tenant_id, mailbox_id)
    await create_audit_log(db=db, entity_type="mailbox", entity_id=mb.id, action="deleted", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"name": mb.name})
    await db.delete(mb)
    await db.commit()
    return Response(status_code=204)


@router.post("/{mailbox_id}/test", response_model=MailboxTestResult)
async def test_mailbox(mailbox_id: int, db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.require_admin),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    mb = await _get(db, tenant_id, mailbox_id)
    return await asyncio.to_thread(email_intake.test_connection, mb)
