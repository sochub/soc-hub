"""Case task and playbook mutations shared by the HTTP API, the copilot (and the MCP server).

All functions only flush; the caller commits.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case, TimelineEvent
from app.models.case_task import CaseTask
from app.models.playbook import PlaybookTemplate
from app.utils.audit import create_audit_log
from app.utils.playbooks import apply_playbook_to_case


async def update_case_task(db: AsyncSession, *, case: Case, task: CaseTask, data: dict, user_id: int) -> CaseTask:
    """Apply phase/title/description/status changes (status only 'todo'/'done'; None = leave as is).

    Completing or reopening stamps completed_at/by and adds a timeline entry. No-op when nothing changed."""
    diff = {}
    for field in ("phase", "title", "description", "status"):
        value = data.get(field)
        if value is None or value == getattr(task, field) or (field == "status" and value not in ("todo", "done")):
            continue
        diff[field] = {"from": getattr(task, field), "to": value}
        setattr(task, field, value)
    if not diff:
        return task
    status_action = None
    if "status" in diff:
        if task.status == "done":
            task.completed_at = datetime.now(timezone.utc)
            task.completed_by = user_id
            status_action = "task_completed"
        else:
            task.completed_at = None
            task.completed_by = None
            status_action = "task_reopened"
        verb = "completed" if task.status == "done" else "reopened"
        db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="task",
                             content=f"Task {verb}: **{task.title}** ({task.phase.replace('_', ' ')})"))
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action=status_action or "task_updated",
                           tenant_id=case.tenant_id, user_id=user_id, changes={"task": task.title, **diff})
    await db.flush()
    return task


async def apply_playbook(db: AsyncSession, *, case: Case, template_id: int, tenant_id: int, user_id: int) -> int:
    """Copy a tenant template's tasks onto the case (dedupe by phase+title); audit + timeline entry.

    Returns the number of tasks added. Raises 404 when the template is not in the tenant."""
    n = await apply_playbook_to_case(db, case=case, template_id=template_id, tenant_id=tenant_id)
    template_name = (await db.execute(
        select(PlaybookTemplate.name).where(PlaybookTemplate.id == template_id))).scalars().first() or "playbook"
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="playbook_applied",
                           tenant_id=tenant_id, user_id=user_id,
                           changes={"playbook": template_name, "tasks_added": n})
    db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="task",
                         content=f"Playbook applied: **{template_name}** ({n} tasks added)"))
    await db.flush()
    return n
