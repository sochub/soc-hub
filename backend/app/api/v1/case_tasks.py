from typing import Any, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.case import Case
from app.models.case_task import CaseTask
from app.models.playbook import PlaybookTemplate
from app.models.user import User
from app.schemas.case_task import CaseTaskCreate, CaseTaskUpdate, CaseTaskOut
from app.utils.audit import create_audit_log

router = APIRouter()


async def _get_case(db: AsyncSession, case_id: int, tenant_id: int) -> Case:
    res = await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))
    case = res.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/{case_id}/tasks", response_model=List[CaseTaskOut])
async def list_tasks(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _get_case(db, case_id, tenant_id)
    res = await db.execute(
        select(CaseTask).where(CaseTask.case_id == case_id)
        .order_by(CaseTask.order, CaseTask.id)
    )
    return res.scalars().all()


@router.post("/{case_id}/tasks", response_model=CaseTaskOut, status_code=201)
async def add_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    body: CaseTaskCreate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _get_case(db, case_id, tenant_id)
    max_order = (await db.execute(
        select(func.max(CaseTask.order)).where(CaseTask.case_id == case_id)
    )).scalar() or 0
    task = CaseTask(
        case_id=case_id, tenant_id=tenant_id, phase=body.phase,
        title=body.title, description=body.description, status="todo", order=max_order + 1,
    )
    db.add(task)
    await db.flush()
    await create_audit_log(
        db=db, entity_type="case", entity_id=case_id, action="task_added",
        tenant_id=tenant_id, user_id=current_user.id,
        changes={"title": task.title, "phase": task.phase},
    )
    await db.commit()
    await db.refresh(task)
    return task


@router.put("/{case_id}/tasks/{task_id}", response_model=CaseTaskOut)
async def update_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    task_id: int,
    body: CaseTaskUpdate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    res = await db.execute(
        select(CaseTask).where(
            CaseTask.id == task_id, CaseTask.case_id == case_id, CaseTask.tenant_id == tenant_id)
    )
    task = res.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if body.phase is not None:
        task.phase = body.phase
    if body.title is not None:
        task.title = body.title
    if body.description is not None:
        task.description = body.description
    status_action = None
    if body.status is not None and body.status in ("todo", "done"):
        task.status = body.status
        if body.status == "done":
            task.completed_at = datetime.now(timezone.utc)
            task.completed_by = current_user.id
            status_action = "task_completed"
        else:
            task.completed_at = None
            task.completed_by = None
            status_action = "task_reopened"

    await create_audit_log(
        db=db, entity_type="case", entity_id=case_id,
        action=status_action or "task_updated",
        tenant_id=tenant_id, user_id=current_user.id,
        changes={"title": task.title, "phase": task.phase},
    )
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/{case_id}/tasks/{task_id}", status_code=204)
async def delete_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    task_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    res = await db.execute(
        select(CaseTask).where(
            CaseTask.id == task_id, CaseTask.case_id == case_id, CaseTask.tenant_id == tenant_id)
    )
    task = res.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task_title = task.title
    await db.delete(task)
    await create_audit_log(
        db=db, entity_type="case", entity_id=case_id, action="task_removed",
        tenant_id=tenant_id, user_id=current_user.id, changes={"title": task_title},
    )
    await db.commit()


@router.post("/{case_id}/apply-playbook/{template_id}", response_model=List[CaseTaskOut])
async def apply_playbook(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    template_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Copy a tenant template's tasks onto the case (dedupe by phase+title)."""
    case = await _get_case(db, case_id, tenant_id)
    res = await db.execute(
        select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks))
        .where(PlaybookTemplate.id == template_id, PlaybookTemplate.tenant_id == tenant_id)
    )
    template = res.scalars().first()
    if not template:
        raise HTTPException(status_code=404, detail="Playbook not found in your tenant. Import it first.")

    existing = await db.execute(
        select(CaseTask.phase, CaseTask.title).where(CaseTask.case_id == case_id)
    )
    have = {(p, t) for p, t in existing.all()}
    base_order = (await db.execute(
        select(func.max(CaseTask.order)).where(CaseTask.case_id == case_id)
    )).scalar() or 0

    n = 0
    for task in template.tasks:
        if (task.phase, task.title) in have:
            continue
        n += 1
        db.add(CaseTask(
            case_id=case_id, tenant_id=tenant_id, phase=task.phase, title=task.title,
            description=task.description, status="todo", order=base_order + n,
            source_template_id=template.id,
        ))
        have.add((task.phase, task.title))

    case.playbook_template_id = template.id
    await create_audit_log(
        db=db, entity_type="case", entity_id=case_id, action="playbook_applied",
        tenant_id=tenant_id, user_id=current_user.id,
        changes={"playbook": template.name, "tasks_added": n},
    )
    await db.commit()

    res = await db.execute(
        select(CaseTask).where(CaseTask.case_id == case_id).order_by(CaseTask.order, CaseTask.id)
    )
    return res.scalars().all()

