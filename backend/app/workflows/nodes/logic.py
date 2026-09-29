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
