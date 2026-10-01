"""RDAP via the rdap.org bootstrap redirector; each redirect hop is SSRF-vetted."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, urljoin, urlsplit

from app.enrichment.sources.base import LookupResult, SourceError, guarded, http_client, parse_json, status_result
from app.workflows.ssrf import SSRFError, assert_url_allowed

NAME = "rdap"
TYPES = frozenset({"domain", "ip"})
_MAX_HOPS = 3


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _registrar(data: dict) -> Optional[str]:
    for ent in data.get("entities") or []:
        if "registrar" in (ent.get("roles") or []):
            for item in ((ent.get("vcardArray") or [None, []])[1] or []):
                if isinstance(item, list) and item and item[0] == "fn":
                    return item[-1]
    return None


async def _lookup(itype, value, transport, now):
    kind = "domain" if itype == "domain" else "ip"
    link = f"https://rdap.org/{kind}/{quote(value, safe='')}"
    url = link
    async with http_client(transport) as c:
        for _ in range(_MAX_HOPS + 1):
            resp = await c.get(url, headers={"Accept": "application/rdap+json"})
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            nxt = urljoin(url, resp.headers.get("location", ""))
            if urlsplit(nxt).scheme != "https":
                raise SourceError("rdap redirect refused")
            try:
                assert_url_allowed(nxt, [])
            except SSRFError:
                raise SourceError("rdap redirect refused") from None
            url = nxt
        else:
            raise SourceError("rdap redirect refused")
    if resp.status_code == 404:
        return LookupResult(status="not_found")
    bad = status_result(NAME, resp)
    if bad:
        return bad
    data = parse_json(resp)
    if itype == "ip":
        rng = f"{data.get('startAddress')} - {data.get('endAddress')}" if data.get("startAddress") else None
        summary = {k: v for k, v in {"name": data.get("name"), "country": data.get("country"),
                                      "handle": data.get("handle"), "range": rng}.items() if v}
        return LookupResult(status="ok", verdict="unknown", summary=summary, link=link)
    created = next((e.get("eventDate") for e in data.get("events") or [] if e.get("eventAction") == "registration"), None)
    dt = _parse_dt(created) if created else None
    verdict = "suspicious" if dt and now - dt < timedelta(days=30) else "unknown"
    summary = {k: v for k, v in {"registrar": _registrar(data), "created": created}.items() if v}
    return LookupResult(status="ok", verdict=verdict, summary=summary, link=link)


async def lookup(itype, value, key=None, *, transport=None, now=None) -> LookupResult:
    return await guarded(NAME, _lookup(itype, value, transport, now or datetime.now(timezone.utc)))
