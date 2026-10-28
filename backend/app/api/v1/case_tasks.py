from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Case
from app.models.case_task import CaseTask
from app.models.user import User
from app.schemas.case_task import CaseTaskCreate, CaseTaskUpdate, CaseTaskOut
from app.services import task_service
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
    return await task_service.task_rows(db, res.scalars().all())


@router.post("/{case_id}/tasks", response_model=CaseTaskOut, status_code=201)
async def add_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    body: CaseTaskCreate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    case = await _get_case(db, case_id, tenant_id)
    task = await task_service.add_case_task(db, case=case, phase=body.phase, title=body.title,
                                            description=body.description, user_id=current_user.id,
                                            workflow_id=body.workflow_id, assignee_id=body.assignee_id)
    await db.commit()
    await db.refresh(task)
    return (await task_service.task_rows(db, [task]))[0]


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
    case = await _get_case(db, case_id, tenant_id)
    if "workflow_id" in body.model_fields_set and body.workflow_id != task.workflow_id:
        await task_service.check_task_workflows(db, tenant_id, [body.workflow_id])
        await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="task_workflow_linked",
                               tenant_id=tenant_id, user_id=current_user.id,
                               changes={"task": task.title, "workflow_id": {"from": task.workflow_id, "to": body.workflow_id}})
        task.workflow_id = body.workflow_id  # null unlinks
    if "assignee_id" in body.model_fields_set:
        await task_service.assign_task(db, case=case, task=task, assignee_id=body.assignee_id, user_id=current_user.id)
    await task_service.update_case_task(db, case=case, task=task, data=body.model_dump(), user_id=current_user.id)
    await db.commit()
    await db.refresh(task)
    return (await task_service.task_rows(db, [task]))[0]


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
    await task_service.delete_case_task(db, task=task, user_id=current_user.id)
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
    await task_service.apply_playbook(db, case=case, template_id=template_id, tenant_id=tenant_id,
                                      user_id=current_user.id)
    await db.commit()

    res = await db.execute(
        select(CaseTask).where(CaseTask.case_id == case_id).order_by(CaseTask.order, CaseTask.id)
    )
    return await task_service.task_rows(db, res.scalars().all())


@router.post("/{case_id}/tasks/{task_id}/run")
async def run_task_workflow(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    task_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Run the workflow linked to this task on its case. Poll /workflow-runs/{run_id} for progress."""
    from app.tasks.workflows import advance_run_task
    case = await _get_case(db, case_id, tenant_id)
    task = (await db.execute(select(CaseTask).where(
        CaseTask.id == task_id, CaseTask.case_id == case_id, CaseTask.tenant_id == tenant_id))).scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    run = await task_service.run_task_workflow(db, case=case, task=task, user_id=current_user.id)
    run_id = run.id
    await db.commit()
    advance_run_task.delay(run_id)
    return {"run_id": run_id}

