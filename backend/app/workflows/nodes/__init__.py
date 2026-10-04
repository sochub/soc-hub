from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from sqlalchemy import select


from app.workflows.errors import NodeError, RetryableNodeError  # noqa: F401  (re-exported)


WAIT = object()  # returned by nodes that pause the step (slack_ask_user, for_each)


@dataclass
class NodeContext:
    db: Any
    run: Any
    step: Any
    node: dict
    ctx: dict
    after_commit: List[Callable[[], None]] = field(default_factory=list)
    # Nonce bound into this step's secret placeholders; send-time resolution only honours this one.
    secret_nonce: Optional[str] = None
    # Names the step's SecretsNamespace handed out (live set); only these resolve at send time.
    secret_names: Optional[Set[str]] = None


EXECUTORS: Dict[str, Callable[[NodeContext, dict], Awaitable[Any]]] = {}


def executor(type_name: str):
    def deco(fn):
        EXECUTORS[type_name] = fn
        return fn
    return deco


async def load_target_case(nctx: NodeContext, config: dict):
    from app.models.case import Case
    raw = config.get("case_id") or nctx.run.case_id
    if raw in (None, ""):
        raise NodeError("no case: this run has no case and no case_id was given")
    try:
        case_id = int(raw)
    except (TypeError, ValueError):
        raise NodeError(f"case_id must be an integer, got {raw!r}")
    case = (await nctx.db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == nctx.run.tenant_id))).scalars().first()
    if not case:
        raise NodeError(f"case {case_id} not found")
    return case


# register executors
from app.workflows.nodes import logic, http, cases, alerts, slack  # noqa: E402,F401
