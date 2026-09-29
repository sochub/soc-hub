from app.workflows.nodes import NodeContext, NodeError, executor
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
