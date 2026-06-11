from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.playbook import PlaybookTemplate, PlaybookTaskTemplate
from app.models.user import User
from app.schemas.playbook import (
    PlaybookTemplateCreate, PlaybookTemplateUpdate, PlaybookTemplateOut,
    PlaybookTemplateSummary, PlaybookImportRequest, PlaybookImportResult,
)

router = APIRouter()


async def _task_counts(db: AsyncSession, template_ids: List[int]) -> dict:
    if not template_ids:
        return {}
    rows = await db.execute(
        select(PlaybookTaskTemplate.template_id, func.count())
        .where(PlaybookTaskTemplate.template_id.in_(template_ids))
        .group_by(PlaybookTaskTemplate.template_id)
    )
    return {tid: c for tid, c in rows.all()}


async def _imported_source_ids(db: AsyncSession, tenant_id: int) -> set:
    rows = await db.execute(
        select(PlaybookTemplate.source_template_id).where(
            PlaybookTemplate.tenant_id == tenant_id,
            PlaybookTemplate.source_template_id.isnot(None),
        )
    )
    return {sid for (sid,) in rows.all()}


@router.get("/marketplace", response_model=List[PlaybookTemplateSummary])
async def list_marketplace(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Global marketplace catalog (read-only system templates)."""
    res = await db.execute(
        select(PlaybookTemplate).where(
            PlaybookTemplate.tenant_id.is_(None), PlaybookTemplate.is_system == True  # noqa: E712
        ).order_by(PlaybookTemplate.category, PlaybookTemplate.name)
    )
    templates = res.scalars().all()
    counts = await _task_counts(db, [t.id for t in templates])
    imported = await _imported_source_ids(db, tenant_id)
    return [
        PlaybookTemplateSummary(
            id=t.id, tenant_id=t.tenant_id, name=t.name, category=t.category,
            description=t.description, is_system=t.is_system, source_template_id=t.source_template_id,
            task_count=counts.get(t.id, 0), already_imported=t.id in imported,
        )
        for t in templates
    ]


@router.get("/", response_model=List[PlaybookTemplateSummary])
async def list_tenant_templates(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """The tenant's own playbook templates."""
    res = await db.execute(
        select(PlaybookTemplate).where(PlaybookTemplate.tenant_id == tenant_id)
        .order_by(PlaybookTemplate.category, PlaybookTemplate.name)
    )
    templates = res.scalars().all()
    counts = await _task_counts(db, [t.id for t in templates])
    return [
        PlaybookTemplateSummary(
            id=t.id, tenant_id=t.tenant_id, name=t.name, category=t.category,
            description=t.description, is_system=t.is_system,
            source_template_id=t.source_template_id, task_count=counts.get(t.id, 0),
        )
        for t in templates
    ]


@router.get("/{template_id}", response_model=PlaybookTemplateOut)
async def get_template(
    *,
    db: AsyncSession = Depends(deps.get_db),
    template_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """A template with its tasks (tenant-owned or marketplace)."""
    res = await db.execute(
        select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks))
        .where(PlaybookTemplate.id == template_id)
    )
    t = res.scalars().first()
    if not t or (t.tenant_id is not None and t.tenant_id != tenant_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@router.post("/import", response_model=PlaybookImportResult)
async def import_templates(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: PlaybookImportRequest,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Deep-copy one or more marketplace templates into the tenant. Idempotent."""
    already = await _imported_source_ids(db, tenant_id)
    imported_ids, skipped = [], 0
    for tid in body.template_ids:
        if tid in already:
            skipped += 1
            continue
        res = await db.execute(
            select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks))
            .where(PlaybookTemplate.id == tid, PlaybookTemplate.tenant_id.is_(None),
                   PlaybookTemplate.is_system == True)  # noqa: E712
        )
        src = res.scalars().first()
        if not src:
            skipped += 1
            continue
        copy = PlaybookTemplate(
            tenant_id=tenant_id, name=src.name, category=src.category,
            description=src.description, is_system=False, source_template_id=src.id,
        )
        db.add(copy)
        await db.flush()
        for task in src.tasks:
            db.add(PlaybookTaskTemplate(
                template_id=copy.id, phase=task.phase, title=task.title,
                description=task.description, order=task.order,
            ))
        imported_ids.append(copy.id)
        already.add(tid)
    await db.commit()
    return PlaybookImportResult(imported=len(imported_ids), skipped=skipped, imported_ids=imported_ids)


@router.post("/", response_model=PlaybookTemplateOut)
async def create_template(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: PlaybookTemplateCreate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create a tenant playbook template from scratch."""
    t = PlaybookTemplate(
        tenant_id=tenant_id, name=body.name, category=body.category,
        description=body.description, is_system=False,
    )
    db.add(t)
    await db.flush()
    for i, task in enumerate(body.tasks):
        db.add(PlaybookTaskTemplate(
            template_id=t.id, phase=task.phase, title=task.title,
            description=task.description, order=task.order or i,
        ))
    await db.commit()
    res = await db.execute(
        select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks)).where(PlaybookTemplate.id == t.id)
    )
    return res.scalars().first()


@router.post("/marketplace", response_model=PlaybookTemplateOut)
async def create_marketplace_template(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: PlaybookTemplateCreate,
    current_user: User = Depends(deps.require_super_admin),
) -> Any:
    """Create a GLOBAL marketplace template (system, tenant_id NULL). Super admin only."""
    t = PlaybookTemplate(
        tenant_id=None, is_system=True, name=body.name, category=body.category,
        description=body.description,
    )
    db.add(t)
    await db.flush()
    for i, task in enumerate(body.tasks):
        db.add(PlaybookTaskTemplate(
            template_id=t.id, phase=task.phase, title=task.title,
            description=task.description, order=task.order or i,
        ))
    await db.commit()
    res = await db.execute(
        select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks)).where(PlaybookTemplate.id == t.id)
    )
    return res.scalars().first()


async def _get_editable_template(db, template_id, tenant_id, current_user: User) -> PlaybookTemplate:
    res = await db.execute(
        select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks))
        .where(PlaybookTemplate.id == template_id)
    )
    t = res.scalars().first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if t.is_system or t.tenant_id is None:
        # Marketplace/system templates are editable by super admins only.
        if not current_user.is_super_admin:
            raise HTTPException(status_code=403, detail="Only super admins can edit marketplace templates.")
    elif t.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


@router.put("/{template_id}", response_model=PlaybookTemplateOut)
async def update_template(
    *,
    db: AsyncSession = Depends(deps.get_db),
    template_id: int,
    body: PlaybookTemplateUpdate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    t = await _get_editable_template(db, template_id, tenant_id, current_user)
    if body.name is not None:
        t.name = body.name
    if body.category is not None:
        t.category = body.category
    if body.description is not None:
        t.description = body.description
    if body.tasks is not None:
        for task in list(t.tasks):
            await db.delete(task)
        await db.flush()
        for i, task in enumerate(body.tasks):
            db.add(PlaybookTaskTemplate(
                template_id=t.id, phase=task.phase, title=task.title,
                description=task.description, order=task.order or i,
            ))
    await db.commit()
    res = await db.execute(
        select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks)).where(PlaybookTemplate.id == t.id)
    )
    return res.scalars().first()


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    *,
    db: AsyncSession = Depends(deps.get_db),
    template_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    t = await _get_editable_template(db, template_id, tenant_id, current_user)
    await db.delete(t)
    await db.commit()
