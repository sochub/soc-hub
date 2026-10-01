from typing import Optional
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.case import Case
from app.models.case_task import CaseTask
from app.models.playbook import PlaybookTemplate


async def apply_playbook_to_case(
    db: AsyncSession, *, case: Case, template_id: int, tenant_id: int
) -> int:
    """Copy a tenant template's tasks onto the case (dedupe by phase+title).

    Shared by the `/cases/{id}/apply-playbook/{template_id}` endpoint and the
    Copilot `apply_playbook` action so both go through one implementation.
    Returns the number of tasks added. Caller commits and audit-logs.
    """
    res = await db.execute(
        select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks))
        .where(PlaybookTemplate.id == template_id, PlaybookTemplate.tenant_id == tenant_id)
    )
    template = res.scalars().first()
    if not template:
        raise HTTPException(status_code=404, detail="Playbook not found in your tenant. Import it first.")

    existing = await db.execute(
        select(CaseTask.phase, CaseTask.title).where(CaseTask.case_id == case.id)
    )
    have = {(p, t) for p, t in existing.all()}
    base_order = (await db.execute(
        select(func.max(CaseTask.order)).where(CaseTask.case_id == case.id)
    )).scalar() or 0

    n = 0
    for task in template.tasks:
        if (task.phase, task.title) in have:
            continue
        n += 1
        db.add(CaseTask(
            case_id=case.id, tenant_id=tenant_id, phase=task.phase, title=task.title,
            description=task.description, status="todo", order=base_order + n,
            source_template_id=template.id,
        ))
        have.add((task.phase, task.title))

    case.playbook_template_id = template.id
    return n
