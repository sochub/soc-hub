"""Find plaintext credentials in http_request nodes and rewrite them to `{{ secrets.NAME }}` templates. Pure functions."""
import copy
import re
from typing import List
from urllib.parse import unquote_plus, urlsplit

from app.secrets.resolve import header_value_problem

HEADER_KEYS = {"authorization", "x-api-key", "api-key", "apikey", "x-auth-token", "x-apikey", "private-token"}
QUERY_KEYS = {"apikey", "api_key", "key", "token", "access_token"}
_SCHEME_RE = re.compile(r"^((?:Bearer|Token)\s+)(\S.*)$", re.I | re.S)


def _host(url: str):
    try:
        sp = urlsplit(url)
        host = sp.hostname
    except ValueError:
        return None
    return host if host and "{" not in sp.netloc else None


def suggest_name(host: str, key: str) -> str:
    n = re.sub(r"[^A-Z0-9]", "_", f"{host}_{key}".upper())
    if not n[:1].isalpha():
        n = "S_" + n
    return n[:64]


def _split_header(key: str, value: str):
    """-> (prefix kept in place, literal)"""
    if key.lower() == "authorization":
        m = _SCHEME_RE.match(value)
        if m:
            return m.group(1), m.group(2)
    return "", value


def scan_graph(graph: dict) -> List[dict]:
    out = []
    for node in (graph or {}).get("nodes") or []:
        cfg = node.get("config") or {}
        if node.get("type") != "http_request" or not isinstance(cfg, dict):
            continue
        url = cfg.get("url")
        host = _host(url) if isinstance(url, str) else None
        if not host:
            continue

        def add(location, key, literal):
            out.append({"node_id": node.get("id"), "location": location, "key": key, "host": host,
                        "suggested_name": suggest_name(host, key), "literal": literal})

        headers = cfg.get("headers")
        for k, v in (headers.items() if isinstance(headers, dict) else []):
            if isinstance(k, str) and isinstance(v, str) and k.lower() in HEADER_KEYS and "{{" not in v:
                lit = _split_header(k, v)[1]
                if lit.strip() and not header_value_problem(lit):
                    add("header", k, lit)
        base, _, query = url.partition("#")[0].partition("?")
        # ponytail: one finding per distinct query key (first occurrence); repeats with other values stay plaintext until re-scanned
        seen = set()
        for pair in query.split("&") if query else []:
            k, eq, v = pair.partition("=")
            if eq and k.lower() in QUERY_KEYS and k not in seen and "{{" not in v and unquote_plus(v).strip():
                seen.add(k)
                add("query", k, unquote_plus(v))
    return out


def apply_conversion(graph: dict, items: List[dict]) -> dict:
    """items: findings (node_id, location, key, literal) plus `secret_name`. Returns a modified deep copy."""
    g = copy.deepcopy(graph)
    for it in items:
        tpl = "{{ secrets.%s }}" % it["secret_name"]
        for node in g.get("nodes") or []:
            if node.get("id") != it["node_id"] or node.get("type") != "http_request":
                continue
            cfg = node["config"]
            if it["location"] == "header":
                v = cfg["headers"][it["key"]]
                cfg["headers"][it["key"]] = _split_header(it["key"], v)[0] + tpl
            else:
                url, hash_, frag = cfg["url"].partition("#")
                base, q, query = url.partition("?")
                pairs = []
                for pair in query.split("&"):
                    k, eq, v = pair.partition("=")
                    if eq and k == it["key"] and "{{" not in v and unquote_plus(v) == it["literal"]:
                        pair = f"{k}={tpl}"
                    pairs.append(pair)
                cfg["url"] = base + q + "&".join(pairs) + hash_ + frag
    return g
