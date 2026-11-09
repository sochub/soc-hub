"""MISP attribute lookup. The 'key' is a JSON string {"url", "key", "verify"} (per-tenant server config)."""
import asyncio
import json
import re
from typing import Optional
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.utils.hostguard import validate_outbound_host

from app.enrichment.sources.base import LookupResult, SourceError, guarded, parse_json, status_result, tag_list

NAME = "misp"
TYPES = frozenset({"ip", "domain", "url", "file_hash"})


def check_url(url: str, *, resolve=None) -> Optional[str]:
    """Error message if `url` is not an acceptable MISP server URL (syntax and SSRF host guard), else None."""
    if not url or re.search(r"\s", url):
        return "MISP URL must not contain whitespace"
    try:
        p = urlsplit(url)
        host = p.hostname
        p.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return "MISP URL is not valid"
    if p.scheme not in ("http", "https") or not host:
        return "MISP URL must start with http:// or https://"
    if p.username is not None or p.password is not None or "@" in p.netloc:
        return "MISP URL must not contain credentials"
    return validate_outbound_host(host, settings.MISP_PRIVATE_HOSTS, "MISP_PRIVATE_HOSTS", resolve=resolve)


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


def _tag_names(a: dict):
    """Attribute tags plus event-level tags (Event.Tag, Event.EventTag[].Tag, EventTag[].Tag)."""
    ev = a.get("Event") if isinstance(a.get("Event"), dict) else {}
    for src in (a.get("Tag"), ev.get("Tag")):
        for t in src if isinstance(src, list) else []:
            if isinstance(t, dict):
                yield t.get("name")
    for src in (a.get("EventTag"), ev.get("EventTag")):
        for et in src if isinstance(src, list) else []:
            t = et.get("Tag") if isinstance(et, dict) else None
            if isinstance(t, dict):
                yield t.get("name")


def _tags(attrs: list) -> list:
    names = []
    for a in attrs:
        for n in _tag_names(a):
            if isinstance(n, str) and n not in names:
                names.append(n)
    names.sort(key=lambda n: not n.lower().startswith("tlp:"))  # stable: TLP tags first
    return tag_list(names)


async def _lookup(itype, value, key, transport):
    cfg = parse_cfg(key)
    err = await asyncio.to_thread(check_url, cfg["url"])
    if err:
        raise SourceError("host not allowed" if "private" in err or "resolved" in err else err)
    async with _client(cfg["verify"], transport) as c:
        resp = await c.post(f"{cfg['url']}/attributes/restSearch", json={"value": value, "limit": 50, "includeContext": True, "includeEventTags": True},
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
