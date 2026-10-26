"""Workflow authoring, dry runs and run cancellation, shared by the HTTP API and the MCP server.

Functions only flush unless stated; the caller commits. Save-time checks are ``workflow_errors`` (graph
structure, node configs, trigger filter or cron, and the Jinja AST secret rules); saving an invalid graph is
allowed only while the workflow is disabled.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Alert, Case
from app.models.tenant_secret import TenantSecret
from app.models.workflow import Workflow, WorkflowRun
from app.schemas.workflow import DryRunIn, WorkflowIn
from app.utils.audit import create_audit_log
from app.workflows.runtime import cancel_run, create_run, flush_withdrawals
from app.workflows.validation import workflow_errors


async def secret_names(db: AsyncSession, tenant_id: int) -> set:
    return set((await db.execute(select(TenantSecret.name).where(TenantSecret.tenant_id == tenant_id))).scalars())


async def create_workflow(db: AsyncSession, *, tenant_id: int, user_id: int, body: WorkflowIn) -> Workflow:
    """A new workflow is always saved disabled (version 1); audit ``create``."""
    wf = Workflow(tenant_id=tenant_id, name=body.name, description=body.description, trigger_type=body.trigger_type,
                  trigger_filter=body.trigger_filter, graph=body.graph, enabled=False, version=1, created_by=user_id)
    db.add(wf)
    await db.flush()
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="create", tenant_id=tenant_id,
                           user_id=user_id)
    return wf


async def save_workflow(db, wf: Workflow, *, name, description, trigger_type, trigger_filter, graph, names: set,
                        tenant_id: int, user_id: int) -> None:
    """Shared by PUT /workflows/{id}, secret conversion and MCP: validate (only when enabled), bump version, audit.
    No commit."""
    if wf.enabled:
        errors = workflow_errors(graph, trigger_type, trigger_filter, names)
        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors, "message": "Disable the workflow to save an invalid graph"})
    wf.name, wf.description, wf.trigger_type = name, description, trigger_type
    wf.trigger_filter, wf.graph, wf.version = trigger_filter, graph, wf.version + 1
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="update", tenant_id=tenant_id,
                           user_id=user_id, changes={"version": wf.version})


async def start_dry_run(db: AsyncSession, wf: Workflow, *, body: DryRunIn, tenant_id: int,
                        user_id: int) -> WorkflowRun:
    """Create a dry run (422 on validation errors, 404 on a case/alert outside the tenant). The caller commits,
    then enqueues ``advance_run_task.delay(run.id)``."""
    errors = workflow_errors(wf.graph, wf.trigger_type, wf.trigger_filter, await secret_names(db, tenant_id))
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    case_id = alert_id = None
    if body.case_id is not None:
        if not (await db.execute(select(Case.id).where(Case.id == body.case_id, Case.tenant_id == tenant_id))).first():
            raise HTTPException(status_code=404, detail="Case not found")
        case_id = body.case_id
    if body.alert_id is not None:
        alert = (await db.execute(select(Alert).where(Alert.id == body.alert_id, Alert.tenant_id == tenant_id))).scalars().first()
        if not alert:
            raise HTTPException(status_code=404, detail="Alert not found")
        alert_id, case_id = alert.id, case_id or alert.case_id
    payload = body.payload or {"event": wf.trigger_type, "case_id": case_id, "alert_id": alert_id, "changes": {}}
    return await create_run(db, wf, trigger_payload=payload, case_id=case_id, alert_id=alert_id, is_dry_run=True,
                            dry_run_mocks={"mocks": body.mocks, "ask_user_answers": body.ask_user_answers},
                            user_id=user_id)


async def cancel_run_by_user(db: AsyncSession, run: WorkflowRun, *, tenant_id: int, user_id: int) -> None:
    """Cancel a run (load it ``with_for_update`` first) and audit it. The caller commits, then calls
    ``after_cancel_commit``."""
    await cancel_run(db, run)
    await create_audit_log(db=db, entity_type="workflow_run", entity_id=run.id, action="cancel", tenant_id=tenant_id,
                           user_id=user_id)


def after_cancel_commit(db: AsyncSession, run: WorkflowRun) -> None:
    flush_withdrawals(db)
    if run.parent_step_id:  # a cancelled loop child must wake its parent for_each
        from app.tasks.workflows import loop_tick_task
        loop_tick_task.delay(run.parent_step_id)
