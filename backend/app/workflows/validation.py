"""Pure save-time validation of a workflow graph."""
import re
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from app.secrets.refs import NAME_RE as SECRET_NAME_RE
from app.services.slack_service import parse_button_labels
from app.workflows.loops import MAX_CONCURRENCY, MAX_ITEMS_CAP
from app.workflows.node_types import NODE_TYPES, TRIGGER_TYPES, MAX_NODES
from jinja2 import nodes as jnodes
from jinja2.parser import Parser

from app.workflows.templating import _env, syntax_errors

_ID_RE = re.compile(r"^[a-z0-9_]+$")


_SECRETS_ONLY_HTTP = "Secrets are only allowed in HTTP request URL, headers and body"
_SECRETS_NO_TRANSFORM = "secrets can only be inserted, not transformed"
_HTTP_SECRET_KEYS = ("url", "headers", "body")


def _err(errors: List[dict], node_id: Optional[str], message: str) -> None:
    errors.append({"node_id": node_id, "message": message})


def _sanitize_graph(graph: Any, errors: List[dict]) -> tuple[List[dict], List[dict]]:
    """Validate graph structure and return sanitized nodes/edges lists.

    Returns (nodes, edges) with malformed items filtered out and errors reported.
    """
    # Ensure graph is a dict
    if not isinstance(graph, dict):
        _err(errors, None, "graph must be a dict")
        return [], []

    # Ensure nodes and edges are lists
    nodes_raw = graph.get("nodes", [])
    edges_raw = graph.get("edges", [])

    if not isinstance(nodes_raw, list):
        _err(errors, None, "nodes must be a list")
        nodes_raw = []
    if not isinstance(edges_raw, list):
        _err(errors, None, "edges must be a list")
        edges_raw = []

    # Filter and validate nodes
    nodes: List[dict] = []
    for i, node in enumerate(nodes_raw):
        if not isinstance(node, dict):
            _err(errors, None, f"node at index {i} must be a dict")
            continue

        # Validate node structure
        node_id = node.get("id")
        node_type = node.get("type")
        node_config = node.get("config")

        if not isinstance(node_id, str):
            _err(errors, None, f"node id must be a string (got {type(node_id).__name__} at index {i})")
            continue

        if not isinstance(node_type, str):
            _err(errors, None, f"node type must be a string for node '{node_id}'")
            continue

        if node_config is not None and not isinstance(node_config, dict):
            _err(errors, node_id, f"node config must be a dict (got {type(node_config).__name__})")
            continue

        nodes.append(node)

    # Filter and validate edges
    edges: List[dict] = []
    for i, edge in enumerate(edges_raw):
        if not isinstance(edge, dict):
            _err(errors, None, f"edge at index {i} must be a dict")
            continue

        # Validate edge structure
        edge_source = edge.get("source")
        edge_target = edge.get("target")
        edge_handle = edge.get("source_handle")

        if not isinstance(edge_source, str):
            _err(errors, None, f"edge source must be a string (got {type(edge_source).__name__} at index {i})")
            continue

        if not isinstance(edge_target, str):
            _err(errors, None, f"edge target must be a string (got {type(edge_target).__name__} at index {i})")
            continue

        if edge_handle is not None and not isinstance(edge_handle, str):
            _err(errors, None, f"edge source_handle must be None or a string (got {type(edge_handle).__name__} at index {i})")
            continue

        edges.append(edge)

    return nodes, edges


def _has_cycle(node_ids: List[str], edges: List[dict]) -> bool:
    """Detect cycles using iterative DFS to avoid RecursionError on deep graphs."""
    adj: Dict[str, List[str]] = {nid: [] for nid in node_ids}
    for e in edges:
        if e["source"] in adj and e["target"] in adj:
            adj[e["source"]].append(e["target"])

    WHITE, GREY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}

    # Iterative DFS using explicit stack to avoid RecursionError
    for start_node in node_ids:
        if color[start_node] != WHITE:
            continue

        stack = [(start_node, False)]  # (node, children_processed)

        while stack:
            u, children_processed = stack.pop()

            if children_processed:
                color[u] = BLACK
                continue

            if color[u] == GREY:
                # Back edge found - cycle detected
                return True

            if color[u] != WHITE:
                continue

            color[u] = GREY
            stack.append((u, True))

            # Add children in reverse order to maintain DFS order
            for v in reversed(adj[u]):
                if color[v] == GREY:
                    # Back edge found
                    return True
                if color[v] == WHITE:
                    stack.append((v, False))

    return False


def _walk_strings(value: Any, is_key: bool = False) -> Iterator[Tuple[str, bool]]:
    """Yield (string, is_dict_key) for every string in a nested config value."""
    if isinstance(value, str):
        yield value, is_key
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(str(k), True)
            yield from _walk_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk_strings(v)


# Blocks whose body output is captured/transformed rather than emitted as-is.
_CAPTURING = (jnodes.AssignBlock, jnodes.FilterBlock, jnodes.Macro, jnodes.CallBlock)


def _parse(text: str, raw: bool):
    """Jinja AST for a templated string (Template) or a raw expression (wrapped in Output); None if
    there is nothing to evaluate or it doesn't parse (syntax_errors reports that separately)."""
    try:
        if raw:
            parser = Parser(_env, text, state="variable")
            expr = parser.parse_expression()
            if not parser.stream.eos:
                return None
            return jnodes.Output([expr])
        if "{{" not in text and "{%" not in text:  # rendered verbatim at run time
            return None
        return _env.parse(text)
    except Exception:
        return None


def _secret_uses(tree) -> List[Tuple[Optional[str], bool]]:
    """(name or None, ok) for every use of the `secrets` variable in a parsed template.

    ok only for `secrets.NAME` emitted directly by an output tag, alone or as an operand of `~`
    concatenation whose result is itself emitted directly. Anything else (item access, filters, tests,
    calls, comparisons, arithmetic, conditionals, {% set %}/{% if %}/{% for %}/macros, bare `secrets`)
    transforms or moves the secret. `x.secrets.y` is an attribute of `x`, not the variable, so it is ignored.
    """
    out: List[Tuple[Optional[str], bool]] = []

    def emitted(node, parents) -> bool:
        i = len(parents) - 1
        while i >= 0 and isinstance(parents[i], jnodes.Concat) and node in parents[i].nodes:
            node, i = parents[i], i - 1
        if i < 0 or not isinstance(parents[i], jnodes.Output) or node not in parents[i].nodes:
            return False
        return not any(isinstance(p, _CAPTURING) for p in parents[:i])

    def walk(node, parents):
        if isinstance(node, jnodes.Name) and node.name == "secrets":
            parent = parents[-1] if parents else None
            if (node.ctx == "load" and isinstance(parent, jnodes.Getattr) and parent.node is node):
                out.append((parent.attr, emitted(parent, parents[:-1])))
            else:
                out.append((None, False))
        for child in node.iter_child_nodes():
            walk(child, parents + [node])

    walk(tree, [])
    return out


def _string_uses(text: str, raw: bool) -> List[Tuple[Optional[str], bool]]:
    tree = _parse(text, raw)
    return _secret_uses(tree) if tree is not None else []


def secret_names_in_graph(graph: Any) -> Set[str]:
    """Names referenced as `secrets.NAME` anywhere in a workflow graph's node configs."""
    names: Set[str] = set()
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or not isinstance(node.get("config"), dict):
            continue
        t = node.get("type")
        raw_keys = (NODE_TYPES.get(t) if isinstance(t, str) else None or {}) or {}
        raw_keys = raw_keys.get("raw", [])
        for key, value in node["config"].items():
            for text, _ in _walk_strings(value):
                names.update(n for n, _ in _string_uses(text, key in raw_keys) if n)
    return names


def _check_secrets(errors: List[dict], node: dict, secret_names: Optional[Set[str]]) -> None:
    nid, cfg = node["id"], node.get("config") or {}
    seen: Set[str] = set()
    raw_keys = NODE_TYPES[node["type"]]["raw"]

    def report(msg: str) -> None:
        if msg not in seen:
            seen.add(msg)
            _err(errors, nid, msg)

    def scan(text: str, is_key: bool, allowed: bool, raw: bool) -> None:
        uses = _string_uses(text, raw)
        if any(not ok for _, ok in uses):
            report(_SECRETS_NO_TRANSFORM)
        if uses and (not allowed or is_key):
            report(_SECRETS_ONLY_HTTP)
            return
        for name, _ in uses:
            if name is None:
                continue
            if not SECRET_NAME_RE.match(name):
                report(f"Invalid secret name {name}")
            elif secret_names is not None and name not in secret_names:
                report(f"Unknown secret {name}")

    is_http = node.get("type") == "http_request"
    for key, value in cfg.items():
        allowed = is_http and key in _HTTP_SECRET_KEYS
        for text, is_key in _walk_strings(value):
            scan(text, is_key, allowed, key in raw_keys)


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
    if node["type"] == "http_request" and cfg.get("headers") is not None and not isinstance(cfg["headers"], dict):
        _err(errors, nid, "Headers must be a JSON object")
    if node["type"] == "slack_ask_user":
        try:
            if len(parse_button_labels(cfg.get("buttons"))) > 5:
                _err(errors, nid, "at most 5 buttons")
        except ValueError as exc:
            _err(errors, nid, str(exc))
        th = cfg.get("timeout_hours")
        if th not in (None, ""):
            try:
                ok = not isinstance(th, bool) and 0 < float(th) <= 168
            except (TypeError, ValueError, OverflowError):
                ok = False
            if not ok:
                _err(errors, nid, "timeout_hours must be between 0 and 168")
    for key, value in cfg.items():
        for msg in syntax_errors(value, raw=key in spec["raw"]):
            _err(errors, nid, f"template error in '{key}': {msg}")


def validate_graph(graph: Any, trigger_type: str, secret_names: Optional[Set[str]] = None) -> List[dict]:
    """Validate a workflow graph. Returns list of error dicts, empty list if valid.

    Handles malformed input gracefully by returning errors instead of raising.
    """
    errors: List[dict] = []

    # Shape validation and sanitization
    nodes, edges = _sanitize_graph(graph, errors)

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
        _check_secrets(errors, node, secret_names)

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

    # Loop rules (for_each bodies)
    for node in nodes:
        nid = node["id"]
        pid = node.get("parent_id")
        if pid is not None and not isinstance(pid, str):
            _err(errors, nid, "parent_id must be a string")
        elif pid:
            parent = by_id.get(pid)
            if not parent or parent.get("type") != "for_each":
                _err(errors, nid, "parent_id must reference a for_each node")
            if node.get("type") == "for_each":
                _err(errors, nid, "for_each loops cannot be nested")
            if node.get("type") == "trigger":
                _err(errors, nid, "the trigger cannot be inside a loop")
        if node.get("type") == "for_each":
            cfg = node.get("config") or {}
            if not any(c.get("parent_id") == nid for c in nodes):
                _err(errors, nid, "loop body is empty — drop nodes inside the loop")
            for key, hi in (("concurrency", MAX_CONCURRENCY), ("max_items", MAX_ITEMS_CAP)):
                if cfg.get(key) not in (None, ""):
                    try:
                        ok = not isinstance(cfg[key], bool) and 1 <= int(cfg[key]) <= hi
                    except (TypeError, ValueError, OverflowError):
                        ok = False
                    if not ok:
                        _err(errors, nid, f"{key} must be between 1 and {hi}")

    for e in edges:
        src, tgt = by_id.get(e["source"]), by_id.get(e["target"])
        if src and tgt and src.get("parent_id") != tgt.get("parent_id"):
            _err(errors, None, f"edge {e.get('id')} crosses a loop boundary")

    # Skip cycle check if node count exceeds MAX_NODES (error already reported)
    if len(nodes) <= MAX_NODES and _has_cycle(list(by_id.keys()), edges):
        _err(errors, None, "graph contains a cycle")

    # Check for unconnected nodes (orphans)
    # Collect nodes that have incoming edges
    has_incoming = set()
    for e in edges:
        has_incoming.add(e.get("target"))

    for node in nodes:
        nid = node.get("id")
        node_type = node.get("type")
        # Skip if it's a trigger (triggers have no incoming edges by design)
        if node_type == "trigger":
            continue
        # Skip if it has a parent_id (body nodes of loops, etc.)
        if node.get("parent_id"):
            continue
        # Error if this node has no incoming edges
        if nid not in has_incoming:
            _err(errors, nid, "node is not connected (no incoming edge)")

    return errors


def validate_trigger_filter(expr: Optional[str]) -> List[dict]:
    """Syntax-check the optional trigger filter expression."""
    if not expr or not expr.strip():
        return []
    errors = [{"node_id": None, "message": f"trigger filter: {msg}"} for msg in syntax_errors(expr, raw=True)]
    if _string_uses(expr, raw=True):
        errors.append({"node_id": None, "message": _SECRETS_ONLY_HTTP})
    return errors


def workflow_errors(graph: Any, trigger_type: str, trigger_filter: Optional[str],
                    secret_names: Optional[Set[str]] = None) -> List[dict]:
    return validate_trigger_filter(trigger_filter) + validate_graph(graph, trigger_type, secret_names)
