"""Pure save-time validation of a workflow graph."""
import re
from typing import Dict, List, Optional

from app.workflows.node_types import NODE_TYPES, TRIGGER_TYPES, MAX_NODES
from app.workflows.templating import syntax_errors

_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _err(errors: List[dict], node_id: Optional[str], message: str) -> None:
    errors.append({"node_id": node_id, "message": message})


def _has_cycle(node_ids: List[str], edges: List[dict]) -> bool:
    adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}
    for e in edges:
        if e["source"] in adj and e["target"] in adj:
            adj[e["source"]].append(e["target"])
    WHITE, GREY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}

    def visit(u: str) -> bool:
        color[u] = GREY
        for v in adj[u]:
            if color[v] == GREY or (color[v] == WHITE and visit(v)):
                return True
        color[u] = BLACK
        return False

    return any(color[nid] == WHITE and visit(nid) for nid in node_ids)


def _check_node_config(errors: List[dict], node: dict, trigger_type: str) -> None:
    nid, spec, cfg = node["id"], NODE_TYPES[node["type"]], node.get("config") or {}
    for key in spec["required"]:
        if cfg.get(key) in (None, "", []):
            _err(errors, nid, f"missing required field '{key}'")
    if spec["alert_only"] and trigger_type != "alert.ingested":
        _err(errors, nid, f"{node['type']} requires trigger type alert.ingested")
    if node["type"] == "alert_promote":
        mode = cfg.get("mode")
        if mode not in ("new", "link", "group"):
            _err(errors, nid, "mode must be new, link or group")
        if mode in ("new", "group") and not cfg.get("title"):
            _err(errors, nid, "missing required field 'title'")
        if mode == "link" and not cfg.get("case_id"):
            _err(errors, nid, "missing required field 'case_id'")
        if mode == "group" and not cfg.get("group_key"):
            _err(errors, nid, "missing required field 'group_key'")
    for key, value in cfg.items():
        for msg in syntax_errors(value, raw=key in spec["raw"]):
            _err(errors, nid, f"template error in '{key}': {msg}")


def validate_graph(graph: dict, trigger_type: str) -> List[dict]:
    errors: List[dict] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    if trigger_type not in TRIGGER_TYPES:
        _err(errors, None, f"unknown trigger type '{trigger_type}'")
    if len(nodes) > MAX_NODES:
        _err(errors, None, f"too many nodes ({len(nodes)} > {MAX_NODES})")

    seen = set()
    for node in nodes:
        nid = node.get("id", "")
        if not _ID_RE.match(nid):
            _err(errors, nid, f"invalid id '{nid}' (use a-z, 0-9, _)")
        if nid in seen:
            _err(errors, nid, f"duplicate node id '{nid}'")
        seen.add(nid)
        if node.get("type") not in NODE_TYPES:
            _err(errors, nid, f"unknown node type '{node.get('type')}'")
            continue
        _check_node_config(errors, node, trigger_type)

    triggers = [n for n in nodes if n.get("type") == "trigger"]
    if len(triggers) != 1:
        _err(errors, None, "workflow needs exactly one trigger node")

    by_id = {n.get("id"): n for n in nodes}
    for e in edges:
        src, tgt = by_id.get(e.get("source")), by_id.get(e.get("target"))
        if not src or not tgt:
            _err(errors, None, f"edge {e.get('id')} references an unknown node")
            continue
        handle = e.get("source_handle")
        if src.get("type") == "condition":
            if handle not in ("true", "false"):
                _err(errors, src["id"], "condition edges must leave from the true/false handles")
        elif handle is not None:
            _err(errors, src["id"], "only condition nodes have true/false handles")

    if _has_cycle(list(by_id.keys()), edges):
        _err(errors, None, "graph contains a cycle")
    return errors
