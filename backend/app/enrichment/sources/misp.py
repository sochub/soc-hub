"""MISP attribute lookup. The 'key' is a JSON string {"url", "key", "verify"} (per-tenant server config)."""
import json
from typing import Optional

import httpx

from app.enrichment.sources.base import LookupResult, SourceError, guarded, parse_json, status_result, tag_list

NAME = "misp"
TYPES = frozenset({"ip", "domain", "url", "file_hash"})


def parse_cfg(raw) -> dict:
    try:
        cfg = json.loads(raw)
    except (TypeError, ValueError):
        raise SourceError("MISP configuration is invalid") from None
    if not isinstance(cfg, dict) or not cfg.get("url") or not cfg.get("key"):
        raise SourceError("MISP configuration is incomplete")
    return {"url": str(cfg["url"]).strip().rstrip("/"), "key": str(cfg["key"]),
            "verify": cfg.get("verify") is not False}


def secret_parts(raw) -> list:
    """The MISP auth key inside the stored blob, for log/error scrubbing."""
    try:
        cfg = json.loads(raw)
        return [cfg["key"]] if isinstance(cfg, dict) and cfg.get("key") else []
    except (TypeError, ValueError):
        return []


def _client(verify: bool, transport) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0, verify=verify, follow_redirects=False, transport=transport,
                             headers={"User-Agent": "SOC-Hub-Enrichment/1.0"})


def _tags(attrs: list) -> list:
    names = []
    for a in attrs:
        for t in (a.get("Tag") or []) if isinstance(a, dict) else []:
            n = t.get("name") if isinstance(t, dict) else None
            if isinstance(n, str) and n not in names:
                names.append(n)
    names.sort(key=lambda n: not n.lower().startswith("tlp:"))  # stable: TLP tags first
    return tag_list(names)


async def _lookup(itype, value, key, transport):
    cfg = parse_cfg(key)
    async with _client(cfg["verify"], transport) as c:
        resp = await c.post(f"{cfg['url']}/attributes/restSearch", json={"value": value, "limit": 50},
                            headers={"Authorization": cfg["key"], "Accept": "application/json",
                                     "Content-Type": "application/json"})
    bad = status_result(NAME, resp)
    if bad:
        return bad
    body = parse_json(resp).get("response")
    attrs = body.get("Attribute") if isinstance(body, dict) else None
    attrs = [a for a in (attrs or []) if isinstance(a, dict)]
    if not attrs:
        return LookupResult(status="not_found")
    events, seen = [], set()
    for a in attrs:
        eid = a.get("event_id")
        if eid is None or eid in seen:
            continue
        seen.add(eid)
        info = (a.get("Event") or {}).get("info") if isinstance(a.get("Event"), dict) else None
        events.append({"id": eid, "info": info})
    first: Optional[object] = events[0]["id"] if events else None
    return LookupResult(
        status="ok", verdict="suspicious" if any(a.get("to_ids") for a in attrs) else "unknown",
        score=f"{len(attrs)} attributes", summary={"events": events[:5], "tags": _tags(attrs)},
        link=f"{cfg['url']}/events/view/{first}" if first is not None else None)


async def lookup(itype: str, value: str, key: str, *, transport=None) -> LookupResult:
    return await guarded(NAME, _lookup(itype, value, key, transport))
