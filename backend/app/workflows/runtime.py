"""Run lifecycle: create -> advance (schedule ready nodes) -> execute steps -> finalize."""
import copy
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.utils.audit import create_audit_log
from app.workflows.context import build_context
from app.workflows.dryrun import dry_run_behaviour, simulated_output
from app.workflows.events import trigger_matches
from app.workflows.graph import ready_nodes, run_outcome
from app.workflows.loops import aggregate_children, next_children_to_start
from app.workflows.node_types import NODE_TYPES
from app.workflows.nodes import EXECUTORS, NodeContext, NodeError, RetryableNodeError, WAIT
from app.workflows.templating import render, TemplateError

logger = logging.getLogger(__name__)

_ACTIVE_STEP = {"pending", "running", "waiting"}
_TERMINAL_RUN = {"succeeded", "failed", "cancelled"}
_BACKOFF = [10, 60]


def _now():
    return datetime.now(timezone.utc)


def _node(graph: dict, node_id: str) -> dict:
    return next(n for n in graph["nodes"] if n["id"] == node_id)


async def create_run(db, workflow: Workflow, *, trigger_payload: dict, case_id: Optional[int] = None,
                     alert_id: Optional[int] = None, depth: int = 0, is_dry_run: bool = False,
                     dry_run_mocks: Optional[dict] = None, user_id: Optional[int] = None) -> WorkflowRun:
    graph = copy.deepcopy(workflow.graph)
    run = WorkflowRun(tenant_id=workflow.tenant_id, workflow_id=workflow.id, workflow_version=workflow.version,
                      case_id=case_id, alert_id=alert_id, status="running", trigger_payload=trigger_payload,
                      graph_snapshot=graph, depth=depth, is_dry_run=is_dry_run, dry_run_mocks=dry_run_mocks)
    db.add(run)
    await db.flush()
    trigger = next(n for n in graph["nodes"] if n["type"] == "trigger" and not n.get("parent_id"))
    db.add(WorkflowRunStep(run_id=run.id, node_id=trigger["id"], status="succeeded", output=trigger_payload,
                           started_at=_now(), finished_at=_now()))
    await create_audit_log(db=db, entity_type="workflow_run", entity_id=run.id,
                           action="dry_run" if is_dry_run else "start", tenant_id=workflow.tenant_id,
                           user_id=user_id, changes={"workflow": workflow.name, "trigger": trigger_payload})
    await db.flush()
    return run


async def dispatch_event(tenant_id, event_type, case_id, alert_id, changes, depth) -> None:
    from app.tasks.workflows import advance_run_task
    from app.models.case import Alert, Case
    from app.workflows.context import alert_to_dict, case_to_dict

    async with AsyncSessionLocal() as db:
        workflows = (await db.execute(select(Workflow).where(
            Workflow.tenant_id == tenant_id, Workflow.enabled == True, Workflow.trigger_type == event_type,  # noqa: E712
        ))).scalars().all()
        if not workflows:
            return
        case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first() if case_id else None
        alert = (await db.execute(select(Alert).where(Alert.id == alert_id, Alert.tenant_id == tenant_id))).scalars().first() if alert_id else None
        payload = {"event": event_type, "case_id": case_id, "alert_id": alert_id, "changes": changes}
        ctx = {"case": await case_to_dict(db, case), "alert": alert_to_dict(alert), "trigger": payload}

        run_ids = []
        for wf in workflows:
            if trigger_matches(wf.trigger_filter, ctx):
                run = await create_run(db, wf, trigger_payload=payload, case_id=case_id, alert_id=alert_id, depth=depth)
                run_ids.append(run.id)
        await db.commit()
    for rid in run_ids:
        advance_run_task.delay(rid)


async def finalize_run(db, run: WorkflowRun, status: str, error: Optional[str] = None) -> None:
    run.status = status
    run.error = error
    run.finished_at = _now()
    for s in (await db.execute(select(WorkflowRunStep).where(
            WorkflowRunStep.run_id == run.id, WorkflowRunStep.status.in_(_ACTIVE_STEP)))).scalars().all():
        s.status = "cancelled"
        s.finished_at = _now()
    await db.flush()


async def cancel_run(db, run: WorkflowRun) -> None:
    if run.status in _TERMINAL_RUN:
        return
    await finalize_run(db, run, "cancelled")
    children = (await db.execute(select(WorkflowRun).where(WorkflowRun.parent_run_id == run.id))).scalars().all()
    for child in children:
        await cancel_run(db, child)


async def advance_run(run_id: int) -> None:
    from app.tasks.workflows import execute_step_task

    to_execute = []
    finished_child = None
    async with AsyncSessionLocal() as db:
        run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update())).scalars().first()
        if not run or run.status in _TERMINAL_RUN or run.status == "queued":
            return
        steps = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run.id))).scalars().all()
        states = {s.node_id: {"status": s.status, "output": s.output} for s in steps}

        failed = next((s for s in steps if s.status == "failed"), None)
        if failed:
            await finalize_run(db, run, "failed", f"step '{failed.node_id}' failed: {failed.error}")
        else:
            ready, skipped = ready_nodes(run.graph_snapshot, states)
            for nid in skipped:
                db.add(WorkflowRunStep(run_id=run.id, node_id=nid, status="skipped", finished_at=_now()))
                states[nid] = {"status": "skipped", "output": None}
            for nid in ready:
                step = WorkflowRunStep(run_id=run.id, node_id=nid, status="pending")
                db.add(step)
                to_execute.append(step)
                states[nid] = {"status": "pending", "output": None}
            await db.flush()

            outcome = run_outcome(run.graph_snapshot, states)
            if outcome:
                await finalize_run(db, run, outcome)
            elif any(s["status"] == "waiting" for s in states.values()) and \
                    not any(s["status"] in ("pending", "running") for s in states.values()):
                run.status = "waiting"
            else:
                run.status = "running"
        if run.status in _TERMINAL_RUN and run.parent_step_id:
            finished_child = run.parent_step_id
        await db.commit()
        step_ids = [s.id for s in to_execute]

    for sid in step_ids:
        execute_step_task.delay(sid)
    if finished_child:
        from app.tasks.workflows import loop_tick_task
        loop_tick_task.delay(finished_child)


async def execute_step(step_id: int) -> None:
    from app.tasks.workflows import advance_run_task, execute_step_task

    async with AsyncSessionLocal() as db:
        step = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id).with_for_update())).scalars().first()
        if not step or step.status != "pending":
            return
        run = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == step.run_id))).scalars().first()
        if run.status in _TERMINAL_RUN:
            return
        step.status = "running"
        step.started_at = step.started_at or _now()
        await db.commit()

        node = _node(run.graph_snapshot, step.node_id)
        nctx = NodeContext(db=db, run=run, step=step, node=node, ctx={})
        raw_keys = set(NODE_TYPES[node["type"]]["raw"])
        retry_in = None
        try:
            nctx.ctx = await build_context(db, run)
            cfg = node.get("config") or {}
            rendered = {k: (v if k in raw_keys else render(v, nctx.ctx)) for k, v in cfg.items()}
            step.input = rendered
            if run.is_dry_run and dry_run_behaviour(node) == "simulate":
                output = simulated_output(node, rendered, run.dry_run_mocks or {})
            else:
                output = await EXECUTORS[node["type"]](nctx, rendered)
            if output is WAIT:
                step.status = "waiting"
            else:
                step.status, step.output, step.finished_at = "succeeded", output, _now()
        except (NodeError, TemplateError) as e:
            await db.rollback()  # discard partial writes from the failed node
            step = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id))).scalars().first()
            nctx.after_commit.clear()
            retries = int(node.get("retries", 2 if node["type"] == "http_request" else 0))
            if isinstance(e, RetryableNodeError) and step.attempt < min(retries, len(_BACKOFF)):
                retry_in = _BACKOFF[step.attempt]
                step.attempt += 1
                step.status = "pending"
                step.error = f"attempt {step.attempt} failed: {e}"
            elif node.get("continue_on_error"):
                step.status, step.output, step.finished_at = "succeeded", {"error": str(e)}, _now()
            else:
                step.status, step.error, step.finished_at = "failed", str(e), _now()
        except Exception as e:  # bug in an executor — fail the step, keep the worker alive
            logger.exception("workflow step %s crashed", step_id)
            await db.rollback()
            step = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == step_id))).scalars().first()
            nctx.after_commit.clear()
            step.status, step.error, step.finished_at = "failed", f"internal error: {e}", _now()
        await db.commit()

    for fn in nctx.after_commit:
        try:
            fn()
        except Exception:
            logger.exception("after_commit hook failed for step %s", step_id)
    if retry_in is not None:
        execute_step_task.apply_async((step_id,), countdown=retry_in)
    else:  # also for "waiting": advance_run flips the run to status=waiting
        advance_run_task.delay(step.run_id)


async def loop_tick(parent_step_id: int) -> None:
    from app.tasks.workflows import advance_run_task

    start_ids, resume_run = [], None
    async with AsyncSessionLocal() as db:
        step = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.id == parent_step_id).with_for_update())).scalars().first()
        if not step or step.status != "waiting":
            return
        parent = (await db.execute(select(WorkflowRun).where(WorkflowRun.id == step.run_id))).scalars().first()
        if parent.status in _TERMINAL_RUN:
            return
        children = (await db.execute(select(WorkflowRun).where(WorkflowRun.parent_step_id == step.id)
                                     .order_by(WorkflowRun.loop_index))).scalars().all()
        statuses = [c.status for c in children]

        if all(s in _TERMINAL_RUN for s in statuses):
            rows = (await db.execute(select(WorkflowRunStep).where(
                WorkflowRunStep.run_id.in_([c.id for c in children])))).scalars().all()
            outputs = {}
            for r in rows:
                if r.node_id != "__start__":
                    outputs.setdefault(r.run_id, {})[r.node_id] = r.output
            agg = aggregate_children([{"index": c.loop_index, "status": c.status, "steps": outputs.get(c.id, {})} for c in children])
            node = _node(parent.graph_snapshot, step.node_id)
            if agg["failed"] and not node.get("continue_on_error"):
                step.status, step.error = "failed", f"{agg['failed']} of {len(children)} items failed"
                step.output = agg
            else:
                step.status = "succeeded"
                step.output = {**agg, **({"error": f"{agg['failed']} items failed"} if agg["failed"] else {})}
            step.finished_at = _now()
            resume_run = parent.id
        else:
            concurrency = int((step.output or {}).get("concurrency", 5))
            for i in next_children_to_start(statuses, concurrency):
                child = children[i]
                child.status = "running"
                db.add(WorkflowRunStep(run_id=child.id, node_id="__start__", status="succeeded",
                                       output=child.trigger_payload, started_at=_now(), finished_at=_now()))
                start_ids.append(child.id)
        await db.commit()

    for cid in start_ids:
        advance_run_task.delay(cid)
    if resume_run:
        advance_run_task.delay(resume_run)
