"""Pure helpers for for_each: item coercion, child scheduling, aggregation."""
from typing import List, Optional

MAX_ITEMS_CAP = 500
MAX_CONCURRENCY = 20
_TERMINAL = {"succeeded", "failed", "cancelled"}


def coerce_items(value, max_items) -> list:
    if not isinstance(value, list):  # strings/dicts are iterable but never what the author meant
        raise ValueError(f"items must evaluate to a list, got {type(value).__name__}")
    cap = min(int(max_items or 100), MAX_ITEMS_CAP)
    if len(value) > cap:
        raise ValueError(f"{len(value)} items exceeds max_items ({cap})")
    return value


def next_children_to_start(statuses: List[str], concurrency: int) -> List[int]:
    active = sum(1 for s in statuses if s in ("running", "waiting"))
    free = max(0, concurrency - active)
    return [i for i, s in enumerate(statuses) if s == "queued"][:free]


def aggregate_children(children: List[dict]) -> Optional[dict]:
    if any(c["status"] not in _TERMINAL for c in children):
        return None
    results = sorted(({"index": c["index"], "status": c["status"], "steps": c["steps"]} for c in children),
                     key=lambda r: r["index"])
    succeeded = sum(1 for c in children if c["status"] == "succeeded")
    return {"results": results, "succeeded": succeeded, "failed": len(children) - succeeded}
