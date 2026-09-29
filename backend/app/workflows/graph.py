"""Pure DAG readiness logic — the heart of the engine; keep it DB-free."""
from typing import Dict, Optional, Set, Tuple

_RESOLVED = {"succeeded", "skipped"}


def _top_level(graph: dict) -> Dict[str, dict]:
    return {n["id"]: n for n in graph["nodes"] if not n.get("parent_id")}


def _edge_active(edge: dict, source: dict, state: dict) -> bool:
    if state["status"] != "succeeded":
        return False
    if source["type"] == "condition":
        result = bool((state.get("output") or {}).get("result"))
        return edge.get("source_handle") == ("true" if result else "false")
    return True


def ready_nodes(graph: dict, states: Dict[str, dict]) -> Tuple[Set[str], Set[str]]:
    nodes = _top_level(graph)
    incoming: Dict[str, list] = {nid: [] for nid in nodes}
    for e in graph["edges"]:
        if e["source"] in nodes and e["target"] in nodes:
            incoming[e["target"]].append(e)

    states = dict(states)
    ready: Set[str] = set()
    skipped: Set[str] = set()
    changed = True
    while changed:  # repeat so skips cascade down chains
        changed = False
        for nid in nodes:
            if nid in states or nid in ready or not incoming[nid]:
                continue
            parent_states = [states.get(e["source"]) for e in incoming[nid]]
            if any(s is None or s["status"] not in _RESOLVED for s in parent_states):
                continue
            if any(_edge_active(e, nodes[e["source"]], s) for e, s in zip(incoming[nid], parent_states)):
                ready.add(nid)
            else:
                skipped.add(nid)
                states[nid] = {"status": "skipped", "output": None}
                changed = True
    return ready, skipped


def run_outcome(graph: dict, states: Dict[str, dict]) -> Optional[str]:
    if any(s["status"] == "failed" for s in states.values()):
        return "failed"
    nodes = _top_level(graph)
    if all(states.get(nid, {}).get("status") in _RESOLVED for nid in nodes):
        return "succeeded"
    return None


def body_graph(graph: dict, loop_id: str) -> dict:
    body = [{**n, "parent_id": None} for n in graph["nodes"] if n.get("parent_id") == loop_id]
    ids = {n["id"] for n in body}
    edges = [e for e in graph["edges"] if e["source"] in ids and e["target"] in ids]
    targets = {e["target"] for e in edges}
    start = {"id": "__start__", "type": "trigger", "config": {}, "parent_id": None, "position": {"x": 0, "y": 0}}
    edges += [{"id": f"__start__{nid}", "source": "__start__", "target": nid, "source_handle": None}
              for nid in sorted(ids - targets)]
    return {"nodes": [start] + body, "edges": edges}
