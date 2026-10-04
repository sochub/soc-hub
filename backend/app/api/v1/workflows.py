from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Alert, Case
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.schemas.workflow import (AllowlistIn, DryRunIn, ManualRunIn, RunDetail, RunSummary,
                                  WorkflowIn, WorkflowOut, WorkflowSummary)
from app.utils.audit import create_audit_log
from app.workflows.runtime import cancel_run, create_run, flush_withdrawals
from app.workflows.validation import workflow_errors

router = APIRouter()
runs_router = APIRouter()


async def _get_workflow(db, workflow_id: int, tenant_id: int) -> Workflow:
    wf = (await db.execute(select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id))).scalars().first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf


async def _run_stats(db, workflow_ids: List[int]) -> dict:
    if not workflow_ids:
        return {}
    counts = dict((await db.execute(
        select(WorkflowRun.workflow_id, func.count()).where(
            WorkflowRun.workflow_id.in_(workflow_ids), WorkflowRun.parent_run_id.is_(None))
        .group_by(WorkflowRun.workflow_id))).all())
    last = {r.workflow_id: r for r in (await db.execute(
        select(WorkflowRun).where(WorkflowRun.workflow_id.in_(workflow_ids), WorkflowRun.parent_run_id.is_(None))
        .ext(distinct_on(WorkflowRun.workflow_id))
        .order_by(WorkflowRun.workflow_id, WorkflowRun.started_at.desc()))).scalars()}
    return {wid: {"run_count": counts.get(wid, 0),
                  "last_run_status": last[wid].status if wid in last else None,
                  "last_run_at": last[wid].started_at if wid in last else None} for wid in workflow_ids}


async def _secret_names(db, tenant_id: int) -> set:
    return set((await db.execute(select(TenantSecret.name).where(TenantSecret.tenant_id == tenant_id))).scalars())


async def save_workflow(db, wf: Workflow, *, name, description, trigger_type, trigger_filter, graph, names: set,
                        tenant_id: int, user_id: int) -> None:
    """Shared by PUT /workflows/{id} and secret conversion: validate (only when enabled), bump version, audit. No commit."""
    if wf.enabled:
        errors = workflow_errors(graph, trigger_type, trigger_filter, names)
        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors, "message": "Disable the workflow to save an invalid graph"})
    wf.name, wf.description, wf.trigger_type = name, description, trigger_type
    wf.trigger_filter, wf.graph, wf.version = trigger_filter, graph, wf.version + 1
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="update", tenant_id=tenant_id,
                           user_id=user_id, changes={"version": wf.version})


def _summary(wf: Workflow, stats: dict) -> dict:
    return {"id": wf.id, "name": wf.name, "description": wf.description, "enabled": wf.enabled,
            "trigger_type": wf.trigger_type, "version": wf.version, "updated_at": wf.updated_at,
            "created_at": wf.created_at, **stats.get(wf.id, {})}


def _out(wf: Workflow, stats: dict, secret_names: Optional[set] = None) -> dict:
    return {**_summary(wf, stats), "trigger_filter": wf.trigger_filter, "graph": wf.graph,
            "validation_errors": workflow_errors(wf.graph, wf.trigger_type, wf.trigger_filter, secret_names)}


# settings routes are declared BEFORE /{workflow_id} so "settings" isn't parsed as an id
@router.get("/settings/http-allowlist")
async def get_allowlist(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    return {"hosts": tenant.workflow_http_allowlist or []}


@router.put("/settings/http-allowlist")
async def put_allowlist(body: AllowlistIn, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    hosts = sorted({h.strip().lower() for h in body.hosts if h.strip()})
    tenant.workflow_http_allowlist = hosts
    await create_audit_log(db=db, entity_type="tenant", entity_id=tenant_id, action="http_allowlist_update",
                           tenant_id=tenant_id, user_id=current_user.id, changes={"hosts": hosts})
    await db.commit()
    return {"hosts": hosts}


@router.get("/", response_model=List[WorkflowSummary])
async def list_workflows(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_active_user),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wfs = (await db.execute(select(Workflow).where(Workflow.tenant_id == tenant_id).order_by(Workflow.name))).scalars().all()
    stats = await _run_stats(db, [w.id for w in wfs])
    return [_summary(w, stats) for w in wfs]


@router.post("/", response_model=WorkflowOut, status_code=201)
async def create_workflow(body: WorkflowIn, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = Workflow(tenant_id=tenant_id, name=body.name, description=body.description, trigger_type=body.trigger_type,
                  trigger_filter=body.trigger_filter, graph=body.graph, enabled=False, version=1, created_by=current_user.id)
    db.add(wf)
    await db.flush()
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="create", tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    return _out(wf, {}, await _secret_names(db, tenant_id))


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.get_current_active_user),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    return _out(wf, await _run_stats(db, [wf.id]), await _secret_names(db, tenant_id))


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(workflow_id: int, body: WorkflowIn, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    names = await _secret_names(db, tenant_id)
    await save_workflow(db, wf, name=body.name, description=body.description, trigger_type=body.trigger_type,
                        trigger_filter=body.trigger_filter, graph=body.graph, names=names,
                        tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    await db.refresh(wf)
    return _out(wf, await _run_stats(db, [wf.id]), names)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    for run in (await db.execute(select(WorkflowRun).where(WorkflowRun.workflow_id == wf.id))).scalars():
        await cancel_run(db, run)
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="delete", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"name": wf.name})
    await db.delete(wf)
    await db.commit()
    flush_withdrawals(db)
    return Response(status_code=204)


@router.post("/{workflow_id}/validate")
async def validate_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                            current_user: User = Depends(deps.get_current_active_user),
                            tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    return {"errors": workflow_errors(wf.graph, wf.trigger_type, wf.trigger_filter, await _secret_names(db, tenant_id))}


async def _set_enabled(db, wf: Workflow, enabled: bool, user_id: int, tenant_id: int, names: Optional[set] = None) -> None:
    if enabled:
        errors = workflow_errors(wf.graph, wf.trigger_type, wf.trigger_filter, names)
        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors})
    wf.enabled = enabled
    await create_audit_log(db=db, entity_type="workflow", entity_id=wf.id, action="enable" if enabled else "disable",
                           tenant_id=tenant_id, user_id=user_id)
    await db.commit()
    await db.refresh(wf)


@router.post("/{workflow_id}/enable", response_model=WorkflowOut)
async def enable_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                          current_user: User = Depends(deps.require_admin),
                          tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    names = await _secret_names(db, tenant_id)
    await _set_enabled(db, wf, True, current_user.id, tenant_id, names)
    return _out(wf, await _run_stats(db, [wf.id]), names)


@router.post("/{workflow_id}/disable", response_model=WorkflowOut)
async def disable_workflow(workflow_id: int, db: AsyncSession = Depends(deps.get_db),
                           current_user: User = Depends(deps.require_admin),
                           tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    wf = await _get_workflow(db, workflow_id, tenant_id)
    await _set_enabled(db, wf, False, current_user.id, tenant_id)
    return _out(wf, await _run_stats(db, [wf.id]), await _secret_names(db, tenant_id))


@router.post("/{workflow_id}/run")
async def run_manual(workflow_id: int, body: ManualRunIn, db: AsyncSession = Depends(deps.get_db),
                     current_user: User = Depends(deps.require_analyst_or_above),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    from app.tasks.workflows import advance_run_task
    wf = await _get_workflow(db, workflow_id, tenant_id)
    if wf.trigger_type != "manual" or not wf.enabled:
        raise HTTPException(status_code=400, detail="Only enabled manual workflows can be run by hand")
    case = (await db.execute(select(Case).where(Case.id == body.case_id, Case.tenant_id == tenant_id))).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    run = await create_run(db, wf, trigger_payload={"event": "manual", "case_id": case.id, "user_id": current_user.id},
                           case_id=case.id, user_id=current_user.id)
    await db.commit()
    advance_run_task.delay(run.id)
    return {"run_id": run.id}


@router.post("/{workflow_id}/dry-run")
async def dry_run(workflow_id: int, body: DryRunIn, db: AsyncSession = Depends(deps.get_db),
                  current_user: User = Depends(deps.require_admin),
                  tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    from app.tasks.workflows import advance_run_task
    wf = await _get_workflow(db, workflow_id, tenant_id)
    errors = workflow_errors(wf.graph, wf.trigger_type, wf.trigger_filter, await _secret_names(db, tenant_id))
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
    run = await create_run(db, wf, trigger_payload=payload, case_id=case_id, alert_id=alert_id, is_dry_run=True,
                           dry_run_mocks={"mocks": body.mocks, "ask_user_answers": body.ask_user_answers},
                           user_id=current_user.id)
    await db.commit()
    advance_run_task.delay(run.id)
    return {"run_id": run.id}


# ── runs ─────────────────────────────────────────────────────────

async def _get_run(db, run_id: int, tenant_id: int, lock: bool = False) -> WorkflowRun:
    q = select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.tenant_id == tenant_id)
    run = (await db.execute(q.with_for_update() if lock else q)).scalars().first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


async def _names(db, ids) -> dict:
    return dict((await db.execute(select(Workflow.id, Workflow.name).where(Workflow.id.in_(list(ids))))).all()) if ids else {}


@runs_router.get("/", response_model=List[RunSummary])
async def list_runs(workflow_id: Optional[int] = None, case_id: Optional[int] = None, alert_id: Optional[int] = None,
                    status: Optional[str] = None, dry_run: Optional[bool] = None, limit: int = 50,
                    db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_active_user),
                    tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    q = select(WorkflowRun).where(WorkflowRun.tenant_id == tenant_id, WorkflowRun.parent_run_id.is_(None))
    if workflow_id is not None:
        q = q.where(WorkflowRun.workflow_id == workflow_id)
    if case_id is not None:
        q = q.where(WorkflowRun.case_id == case_id)
    if alert_id is not None:
        q = q.where(WorkflowRun.alert_id == alert_id)
    if status:
        q = q.where(WorkflowRun.status == status)
    if dry_run is not None:
        q = q.where(WorkflowRun.is_dry_run == dry_run)
    runs = (await db.execute(q.order_by(WorkflowRun.started_at.desc()).limit(max(1, min(limit, 200))))).scalars().all()
    names = await _names(db, {r.workflow_id for r in runs})
    return [{**RunSummary.model_validate(r).model_dump(), "workflow_name": names.get(r.workflow_id)} for r in runs]


@runs_router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: int, db: AsyncSession = Depends(deps.get_db),
                  current_user: User = Depends(deps.get_current_active_user),
                  tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    run = await _get_run(db, run_id, tenant_id)
    steps = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id).order_by(WorkflowRunStep.id))).scalars().all()
    children = (await db.execute(select(WorkflowRun).where(WorkflowRun.parent_run_id == run.id).order_by(WorkflowRun.loop_index))).scalars().all()
    names = await _names(db, {run.workflow_id})
    return {**RunSummary.model_validate(run).model_dump(), "workflow_name": names.get(run.workflow_id),
            "trigger_payload": run.trigger_payload, "graph_snapshot": run.graph_snapshot,
            "parent_run_id": run.parent_run_id, "loop_index": run.loop_index, "loop_item": run.loop_item,
            "steps": steps, "children": children}


@runs_router.post("/{run_id}/cancel", response_model=RunSummary)
async def cancel(run_id: int, db: AsyncSession = Depends(deps.get_db),
                 current_user: User = Depends(deps.require_analyst_or_above),
                 tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    # row lock: a concurrent advance_run/execute_step/handle_answer can't overwrite the cancel
    run = await _get_run(db, run_id, tenant_id, lock=True)
    await cancel_run(db, run)
    await create_audit_log(db=db, entity_type="workflow_run", entity_id=run.id, action="cancel", tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    flush_withdrawals(db)
    if run.parent_step_id:  # a cancelled loop child must wake its parent for_each
        from app.tasks.workflows import loop_tick_task
        loop_tick_task.delay(run.parent_step_id)
    return run
