from app.models.workflow import WorkflowRun
from app.workflows.graph import body_graph
from app.workflows.loops import MAX_CONCURRENCY, coerce_items
from app.workflows.nodes import NodeContext, NodeError, WAIT, executor
from app.workflows.templating import eval_expr, TemplateError


@executor("trigger")
async def run_trigger(nctx: NodeContext, config: dict) -> dict:
    return nctx.run.trigger_payload or {}


@executor("condition")
async def run_condition(nctx: NodeContext, config: dict) -> dict:
    try:
        return {"result": bool(eval_expr(config["expression"], nctx.ctx))}
    except TemplateError as e:
        raise NodeError(f"condition failed: {e}")


@executor("for_each")
async def run_for_each(nctx: NodeContext, config: dict):
    from app.tasks.workflows import loop_tick_task
    try:
        items = coerce_items(eval_expr(config["items"], nctx.ctx), config.get("max_items"))
    except (TemplateError, ValueError) as e:
        raise NodeError(str(e))
    if not items:
        return {"results": [], "succeeded": 0, "failed": 0}
    concurrency = max(1, min(int(config.get("concurrency") or 5), MAX_CONCURRENCY))
    run, body = nctx.run, body_graph(nctx.run.graph_snapshot, nctx.node["id"])
    for i, item in enumerate(items):
        nctx.db.add(WorkflowRun(
            tenant_id=run.tenant_id, workflow_id=run.workflow_id, workflow_version=run.workflow_version,
            case_id=run.case_id, alert_id=run.alert_id, status="queued", trigger_payload=run.trigger_payload,
            graph_snapshot=body, depth=run.depth, is_dry_run=run.is_dry_run, dry_run_mocks=run.dry_run_mocks,
            parent_run_id=run.id, parent_step_id=nctx.step.id, loop_index=i, loop_item=item))
    nctx.step.output = {"concurrency": concurrency, "total": len(items)}
    step_id = nctx.step.id
    nctx.after_commit.append(lambda: loop_tick_task.delay(step_id))
    return WAIT


@executor("fail")
async def run_fail(nctx: NodeContext, config: dict) -> dict:
    """Stop the run as failed with a message, e.g. when a vendor pull returns 401/429."""
    raise NodeError(str(config.get("message") or "stopped by fail step")[:500])


MAX_CHILD_RUNS_PER_ROOT = 25  # workflows one manual/triggered run may start, across its whole chain


@executor("run_workflow")
async def run_workflow(nctx: NodeContext, config: dict) -> dict:
    """Start another of this organization's manual workflows on the same case (or `case_id`), passing `inputs`
    as `trigger.inputs`. It runs on its own; this step only starts it. Chains stop at MAX_DEPTH, and one root run
    starts at most MAX_CHILD_RUNS_PER_ROOT workflows in total (no exponential fan-out on the shared queue)."""
    from sqlalchemy import func, select
    from app.models.workflow import Workflow
    from app.tasks.workflows import advance_run_task
    from app.workflows.events import MAX_DEPTH
    from app.workflows.nodes import load_target_case
    from app.workflows.runtime import create_run

    run = nctx.run
    if run.parent_step_id is not None:  # a for_each iteration (validation refuses this too)
        raise NodeError("run_workflow can't run inside a for_each loop")
    started_by = (run.trigger_payload or {}).get("started_by") if isinstance(run.trigger_payload, dict) else None
    root = (started_by or {}).get("root_run_id") if run.depth else None
    root = root if isinstance(root, int) and not isinstance(root, bool) else run.id
    raw_id, name = config.get("workflow_id"), str(config.get("workflow_name") or "").strip()
    q = select(Workflow).where(Workflow.tenant_id == run.tenant_id, Workflow.trigger_type == "manual")
    if raw_id not in (None, ""):
        try:
            q = q.where(Workflow.id == int(raw_id))
        except (TypeError, ValueError):
            raise NodeError(f"workflow_id must be an integer, got {raw_id!r}")
    elif name:
        q = q.where(Workflow.name == name).order_by(Workflow.id.desc())  # newest wins if names repeat
    else:
        raise NodeError("set workflow_id or workflow_name")
    wf = (await nctx.db.execute(q.limit(1))).scalars().first()
    label = name or f"#{raw_id}"
    if wf is None:
        raise NodeError(f"no manual workflow {label!r} in this organization: install or create it first")
    if not wf.enabled:
        raise NodeError(f"the workflow {wf.name!r} is disabled: an admin enables it in Workflows")
    if wf.id == run.workflow_id:
        raise NodeError("a workflow can't start itself")
    if run.depth + 1 > MAX_DEPTH:
        raise NodeError(f"too many workflows started in a chain (at most {MAX_DEPTH})")
    case = await load_target_case(nctx, config)
    inputs = config.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise NodeError("inputs must be a JSON object")
    started = (await nctx.db.execute(select(func.count()).select_from(WorkflowRun).where(
        WorkflowRun.tenant_id == run.tenant_id, WorkflowRun.depth > 0,
        WorkflowRun.trigger_payload["started_by"]["root_run_id"].as_integer() == root))).scalar()
    if started >= MAX_CHILD_RUNS_PER_ROOT:
        raise NodeError(f"this run already started {MAX_CHILD_RUNS_PER_ROOT} workflows (the limit per run)")
    parent = (await nctx.db.get(Workflow, run.workflow_id))
    child = await create_run(nctx.db, wf, case_id=case.id, alert_id=run.alert_id, depth=run.depth + 1, trigger_payload={
        "event": "workflow", "case_id": case.id, "inputs": inputs,
        "started_by": {"run_id": run.parent_run_id or run.id, "root_run_id": root, "workflow_id": run.workflow_id,
                       "workflow_name": parent.name if parent else None}})
    child_id = child.id
    nctx.after_commit.append(lambda: advance_run_task.delay(child_id))
    return {"run_id": child_id, "workflow_id": wf.id, "workflow_name": wf.name, "case_id": case.id}
