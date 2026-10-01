"""URLhaus and ThreatFox (abuse.ch); both use the same free Auth-Key."""
from urllib.parse import quote

from app.enrichment.sources.base import LookupResult, guarded, http_client, parse_json, status_result, tag_list

URLHAUS, THREATFOX = "urlhaus", "threatfox"
URLHAUS_TYPES = frozenset({"url", "domain", "ip", "file_hash"})
THREATFOX_TYPES = frozenset({"url", "domain", "ip", "file_hash"})


async def _urlhaus(itype, value, key, transport):
    if itype == "url":
        path, form = "url", {"url": value}
    elif itype == "file_hash":
        if len(value) not in (32, 64):
            return LookupResult(status="not_found")
        path, form = "payload", {("md5_hash" if len(value) == 32 else "sha256_hash"): value}
    else:
        path, form = "host", {"host": value}
    async with http_client(transport) as c:
        resp = await c.post(f"https://urlhaus-api.abuse.ch/v1/{path}/", data=form, headers={"Auth-Key": key})
    bad = status_result(URLHAUS, resp)
    if bad:
        return bad
    data = parse_json(resp)
    if data.get("query_status") != "ok":
        return LookupResult(status="not_found")
    entries = data.get("urls") or ([data] if path in ("url", "payload") else [])
    first = entries[0] if entries else data
    summary = {"threat": first.get("threat") or data.get("signature") or "malware",
               "url_status": first.get("url_status"), "tags": tag_list(first.get("tags")),
               "first_seen": first.get("date_added") or data.get("firstseen")}
    return LookupResult(status="ok", verdict="malicious", summary=summary,
                        link=f"https://urlhaus.abuse.ch/browse.php?search={quote(value, safe='')}")


async def _threatfox(itype, value, key, transport):
    body = {"query": "search_hash", "hash": value} if itype == "file_hash" else {"query": "search_ioc", "search_term": value}
    async with http_client(transport) as c:
        resp = await c.post("https://threatfox-api.abuse.ch/api/v1/", json=body, headers={"Auth-Key": key})
    bad = status_result(THREATFOX, resp)
    if bad:
        return bad
    data = parse_json(resp)
    rows = data.get("data") if data.get("query_status") == "ok" else None
    if not rows or not isinstance(rows, list):
        return LookupResult(status="not_found")
    best = max(rows, key=lambda r: int(r.get("confidence_level") or 0))
    summary = {"malware_printable": best.get("malware_printable"), "threat_type": best.get("threat_type"),
               "confidence_level": best.get("confidence_level"), "first_seen": best.get("first_seen"),
               "tags": tag_list(best.get("tags"))}
    return LookupResult(status="ok", verdict="malicious", score=f"confidence {best.get('confidence_level')}",
                        summary=summary, link=f"https://threatfox.abuse.ch/browse.php?search=ioc%3A{quote(value, safe='')}")


async def lookup_urlhaus(itype, value, key, *, transport=None) -> LookupResult:
    return await guarded(URLHAUS, _urlhaus(itype, value, key, transport))


async def lookup_threatfox(itype, value, key, *, transport=None) -> LookupResult:
    return await guarded(THREATFOX, _threatfox(itype, value, key, transport))
