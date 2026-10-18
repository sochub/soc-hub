"""Tenant audit log: list / facets / export (admins), plus the per-entity change history (any member)."""
import contextlib
import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.audit.context import audit_read, ctx_of
from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas import audit_log as schema

router = APIRouter()
EXPORT_CAP = 50_000
EXPORT_BATCH = 1000


class _Filters:
    def __init__(
        self,
        category: Optional[Literal["change", "write", "security", "read"]] = None,
        action: Optional[str] = Query(None, max_length=200),
        user_id: Optional[int] = None,
        actor: Optional[str] = Query(None, max_length=128),
        entity_type: Optional[str] = Query(None, max_length=64),
        entity_id: Optional[int] = None,
        outcome: Optional[Literal["success", "denied", "error"]] = None,
        ip: Optional[str] = Query(None, max_length=45),
        request_id: Optional[str] = Query(None, max_length=36),
        date_from: Optional[datetime] = Query(None, alias="from"),
        date_to: Optional[datetime] = Query(None, alias="to"),
        scope: Literal["tenant", "platform"] = "tenant",
    ):
        self.__dict__.update(locals())
        del self.__dict__["self"]
        for k in ("date_from", "date_to"):
            v = getattr(self, k)
            if isinstance(v, datetime) and v.tzinfo is None:
                setattr(self, k, v.replace(tzinfo=timezone.utc))


def _like_prefix(v: str) -> str:
    return v.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


async def _scope_clause(f: _Filters, admin: User, tenant_id: int):
    if f.scope == "platform":
        if not admin.is_super_admin:
            raise HTTPException(status_code=403, detail="Not enough permissions")
        return AuditLog.tenant_id.is_(None)
    return AuditLog.tenant_id == tenant_id


async def _query(f: _Filters, admin: User, tenant_id: int):
    q = select(AuditLog).where(await _scope_clause(f, admin, tenant_id))
    if f.category:
        q = q.where(AuditLog.category == f.category)
    if f.action:
        q = q.where(AuditLog.action.ilike(_like_prefix(f.action), escape="\\"))
    if f.user_id is not None:
        q = q.where(AuditLog.user_id == f.user_id)
    if f.actor:
        q = q.where(AuditLog.actor.ilike(_like_prefix(f.actor), escape="\\"))
    if f.entity_type:
        q = q.where(AuditLog.entity_type == f.entity_type)
    if f.entity_id is not None:
        q = q.where(AuditLog.entity_id == f.entity_id)
    if f.outcome:
        q = q.where(AuditLog.outcome == f.outcome)
    if f.ip:
        q = q.where(AuditLog.ip == f.ip)
    if f.request_id:
        q = q.where(AuditLog.request_id == f.request_id)
    if f.date_from:
        q = q.where(AuditLog.created_at >= f.date_from)
    if f.date_to:
        q = q.where(AuditLog.created_at <= f.date_to)
    return q


def _platform_read(request: Request, f: _Filters) -> None:
    """A platform-scope view is a platform event: record it with tenant NULL, not under ?tenant_id."""
    if f.scope == "platform":
        ctx = ctx_of(request)
        if ctx is not None:
            ctx.tenant_id = None


def _csv_safe(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _item(row: AuditLog) -> schema.AuditItem:
    item = schema.AuditItem.model_validate(row, from_attributes=True)
    item.user = (schema.AuditUser(id=row.user.id, email=row.user.email, full_name=row.user.full_name)
                 if row.user is not None else None)
    return item


@router.get("", response_model=schema.AuditPage, dependencies=[audit_read("audit.view")])
async def list_audit_logs(
    request: Request,
    f: _Filters = Depends(),
    before: Optional[int] = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(deps.get_db),
    admin: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    q = await _query(f, admin, tenant_id)
    _platform_read(request, f)
    if before is not None or f.request_id:
        # Only the first page of a plain list is a view: "Load more" and Related lookups don't record reads.
        ctx = ctx_of(request)
        if ctx is not None:
            ctx.read_action = None
    if before is not None:
        q = q.where(AuditLog.id < before)
    q = q.options(selectinload(AuditLog.user)).order_by(AuditLog.id.desc()).limit(limit + 1)
    found = list((await db.execute(q)).scalars().all())
    more = len(found) > limit
    found = found[:limit]
    return schema.AuditPage(items=[_item(r) for r in found], next_before=found[-1].id if more and found else None)


@router.get("/facets", response_model=schema.AuditFacets)
async def audit_facets(
    scope: Literal["tenant", "platform"] = "tenant",
    db: AsyncSession = Depends(deps.get_db),
    admin: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    f = _Filters(scope=scope)
    where = await _scope_clause(f, admin, tenant_id)
    cats = (await db.execute(select(AuditLog.category).where(where).distinct())).scalars().all()
    acts = (await db.execute(select(AuditLog.action).where(where).group_by(AuditLog.action)
                             .order_by(func.count().desc()).limit(200))).scalars().all()
    ents = (await db.execute(select(AuditLog.entity_type).where(where, AuditLog.entity_type.isnot(None))
                             .distinct())).scalars().all()
    uids = select(AuditLog.user_id).where(where, AuditLog.user_id.isnot(None)).distinct()
    users = (await db.execute(select(User).where(User.id.in_(uids)).order_by(User.email))).scalars().all()
    return schema.AuditFacets(categories=sorted(cats), actions=list(acts), entity_types=sorted(ents),
                              users=[schema.AuditUser(id=u.id, email=u.email, full_name=u.full_name) for u in users])


_CSV_FIELDS = ["id", "created_at", "category", "action", "outcome", "status_code", "method", "route",
               "entity_type", "entity_id", "user_email", "actor", "ip", "user_agent", "request_id",
               "duration_ms", "changes"]


@router.get("/export", dependencies=[audit_read("audit.export")])
async def export_audit_logs(
    request: Request,
    f: _Filters = Depends(),
    format: Literal["csv", "json"] = "csv",
    db: AsyncSession = Depends(deps.get_db),
    admin: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    base = await _query(f, admin, tenant_id)  # raises 403 for a non-super-admin platform export up front
    _platform_read(request, f)
    slug = (await db.execute(select(Tenant.slug).where(Tenant.id == tenant_id))).scalar_one_or_none() or "tenant"
    if f.scope == "platform":
        slug = "platform"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # FastAPI runs yield-dependency teardown only after the response is sent, so release the request's
    # connection now rather than holding it for the whole stream.
    await db.close()

    async def rows():
        sent, before = 0, None
        while sent < EXPORT_CAP:
            q = base.order_by(AuditLog.id.desc()).limit(min(EXPORT_BATCH, EXPORT_CAP - sent))
            if before is not None:
                q = q.where(AuditLog.id < before)
            # One short-lived session per batch: no pooled connection is held while the client applies back-pressure.
            async with AsyncSessionLocal() as s:
                batch = list((await s.execute(q)).scalars().all())
            if not batch:
                return
            for r in batch:
                yield r
            sent += len(batch)
            before = batch[-1].id

    def as_dict(r: AuditLog) -> dict:
        d = {k: getattr(r, k) for k in _CSV_FIELDS if k != "changes"}
        d["created_at"] = r.created_at.isoformat() if r.created_at else ""
        d["changes"] = json.dumps(r.changes) if r.changes is not None else ""
        return d

    async def body_csv():
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_CSV_FIELDS)
        w.writeheader()
        async with contextlib.aclosing(rows()) as it:
            async for r in it:
                w.writerow({k: _csv_safe(v) for k, v in as_dict(r).items()})
                if buf.tell() > 64_000:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
        yield buf.getvalue()

    async def body_json():
        yield "["
        first = True
        async with contextlib.aclosing(rows()) as it:
            async for r in it:
                yield ("" if first else ",") + json.dumps(as_dict(r))
                first = False
        yield "]"

    media = "text/csv" if format == "csv" else "application/json"
    return StreamingResponse(body_csv() if format == "csv" else body_json(), media_type=media, headers={
        "Content-Disposition": f'attachment; filename="audit-{slug}-{stamp}.{format}"'})


@router.get("/{entity_type}/{entity_id}", response_model=List[schema.AuditLog])
async def read_audit_logs(
    *,
    db: AsyncSession = Depends(deps.get_db),
    entity_type: str,
    entity_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Change history of one entity (tenant-scoped). Only `change` rows: who-read-what stays admin-only."""
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id,
               AuditLog.tenant_id == tenant_id, AuditLog.category == "change")
        .order_by(AuditLog.created_at.desc())
    )
    return result.scalars().all()
