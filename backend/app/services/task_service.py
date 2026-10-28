"""Case task and playbook mutations shared by the HTTP API, the copilot (and the MCP server).

All functions only flush; the caller commits.
"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import HTTPException

from app.models.case import Case, TimelineEvent
from app.models.case_task import CaseTask
from app.models.membership import TenantMembership
from app.models.user import User
from app.models.playbook import PlaybookTemplate
from app.models.workflow import Workflow, WorkflowRun
from app.utils.audit import create_audit_log
from app.services.war_room_events import queue_card
from app.utils.playbooks import apply_playbook_to_case


async def check_task_workflows(db: AsyncSession, tenant_id: int, workflow_ids) -> None:
    """422 unless every given id is a manual workflow of this tenant (enabled or not: it is checked again at run)."""
    ids = {i for i in workflow_ids if i is not None}
    if not ids:
        return
    ok = set((await db.execute(select(Workflow.id).where(
        Workflow.id.in_(ids), Workflow.tenant_id == tenant_id, Workflow.trigger_type == "manual"))).scalars())
    if ok != ids:
        raise HTTPException(status_code=422, detail="A task can only link a manual workflow of this organization")


async def task_rows(db: AsyncSession, tasks) -> list:
    """CaseTaskOut dicts with the linked workflow's name, whether it can run now, and the last run's status."""
    wf_ids = {t.workflow_id for t in tasks if t.workflow_id}
    run_ids = {t.last_run_id for t in tasks if t.last_run_id}
    wfs = {w.id: w for w in (await db.execute(select(Workflow).where(Workflow.id.in_(wf_ids)))).scalars()} if wf_ids else {}
    runs = dict((await db.execute(select(WorkflowRun.id, WorkflowRun.status).where(
        WorkflowRun.id.in_(run_ids)))).all()) if run_ids else {}
    from app.api.v1.ticketing import link_out
    from app.models.case import CaseLink
    task_ids = [t.id for t in tasks]
    people_ids = {t.assignee_id for t in tasks if t.assignee_id}
    people = dict((await db.execute(select(User.id, func.coalesce(User.full_name, User.email)).where(
        User.id.in_(people_ids)))).all()) if people_ids else {}
    tickets = {lk.task_id: link_out(lk) for lk in (await db.execute(select(CaseLink).where(
        CaseLink.task_id.in_(task_ids or [-1]), CaseLink.connection_id.isnot(None)))).scalars()}
    out = []
    for t in tasks:
        w = wfs.get(t.workflow_id)
        out.append({c: getattr(t, c) for c in ("id", "case_id", "phase", "title", "description", "status", "order",
                                                "completed_at", "completed_by", "assignee_id", "created_at", "workflow_id", "last_run_id",
                                                "result", "result_status", "result_at")}
                   | {"workflow_name": w.name if w else None, "assignee_name": people.get(t.assignee_id),
                      "workflow_runnable": bool(w and w.enabled and w.trigger_type == "manual"),
                      "last_run_status": runs.get(t.last_run_id), "ticket": tickets.get(t.id)})
    return out


async def run_task_workflow(db: AsyncSession, *, case: Case, task: CaseTask, user_id: int) -> WorkflowRun:
    """Start the task's linked workflow on its case (same rules as a manual run). Flushes; the caller commits and
    queues advance_run_task."""
    from app.workflows.runtime import start_manual_run
    wf = (await db.execute(select(Workflow).where(Workflow.id == task.workflow_id,
                                                  Workflow.tenant_id == case.tenant_id))).scalars().first()
    if wf is None:
        raise HTTPException(status_code=409, detail="This task has no workflow to run")
    if wf.trigger_type != "manual" or not wf.enabled:
        raise HTTPException(status_code=409, detail=f"The workflow \"{wf.name}\" is disabled: an admin enables it in Workflows")
    try:
        run = await start_manual_run(db, wf=wf, case=case, user_id=user_id)
    except ValueError:
        raise HTTPException(status_code=409, detail="Only enabled manual workflows can be run by hand") from None
    task.last_run_id = run.id
    db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="task",
                         content=f"Ran **{wf.name}** for task **{task.title}**"))
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="task_workflow_run",
                           tenant_id=case.tenant_id, user_id=user_id,
                           changes={"task": task.title, "workflow": wf.name, "run_id": run.id})
    await db.flush()
    return run


async def assignable_ids(db: AsyncSession, tenant_id: int) -> set:
    """Active analysts and admins of the tenant: who a case or task can be handed to."""
    return set((await db.execute(select(User.id).join(TenantMembership, TenantMembership.user_id == User.id).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.role.in_(["admin", "analyst"]),
        User.is_active.is_(True)))).scalars())


async def assign_task(db: AsyncSession, *, case: Case, task: CaseTask, assignee_id, user_id: int) -> None:
    """Assign (or with None, unassign) a task. 422 unless the assignee is an active analyst or admin here.

    Timeline + audit entry, and the new assignee follows the case and is notified (not when assigning yourself)."""
    if assignee_id == task.assignee_id:
        return
    if assignee_id is not None and assignee_id not in await assignable_ids(db, case.tenant_id):
        raise HTTPException(status_code=422, detail="A task can only be assigned to an active analyst or admin of this organization")
    before, task.assignee_id = task.assignee_id, assignee_id
    name = (await db.execute(select(func.coalesce(User.full_name, User.email)).where(User.id == assignee_id))).scalar() \
        if assignee_id else None
    db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="task",
                         content=f"Task **{task.title}** assigned to {name}" if name else f"Task **{task.title}** unassigned"))
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="task_assigned",
                           tenant_id=case.tenant_id, user_id=user_id,
                           changes={"task": task.title, "assignee_id": {"from": before, "to": assignee_id}})
    await db.flush()
    if assignee_id:
        from app.notifications import service as notify
        await notify.on_task_assigned(db, case, task, assignee_id, user_id)


async def add_case_task(db: AsyncSession, *, case: Case, phase: str, title: str, description, user_id: int,
                        workflow_id=None, assignee_id=None) -> CaseTask:
    """Append a 'todo' task at the end of the case's task order; audit ``task_added``."""
    await check_task_workflows(db, case.tenant_id, [workflow_id])
    max_order = (await db.execute(select(func.max(CaseTask.order)).where(CaseTask.case_id == case.id))).scalar() or 0
    task = CaseTask(case_id=case.id, tenant_id=case.tenant_id, phase=phase, title=title, description=description,
                    status="todo", order=max_order + 1, workflow_id=workflow_id)
    db.add(task)
    await db.flush()
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="task_added",
                           tenant_id=case.tenant_id, user_id=user_id, changes={"title": task.title, "phase": task.phase})
    if assignee_id is not None:
        await assign_task(db, case=case, task=task, assignee_id=assignee_id, user_id=user_id)
    return task


async def delete_case_task(db: AsyncSession, *, task: CaseTask, user_id: int) -> None:
    case_id, tenant_id, title = task.case_id, task.tenant_id, task.title
    await db.delete(task)
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="task_removed",
                           tenant_id=tenant_id, user_id=user_id, changes={"title": title})
    await db.flush()


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
    if status_action == "task_completed":
        total, done = (await db.execute(select(func.count(CaseTask.id), func.count(CaseTask.id).filter(CaseTask.status == "done"))
                                        .where(CaseTask.case_id == case.id))).one()
        queue_card(db, case.tenant_id, "task.completed", case.id,
                   {"title": task.title, "phase": task.phase, "done": done, "total": total, "user_id": user_id})
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
