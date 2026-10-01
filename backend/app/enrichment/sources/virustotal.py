import base64
from urllib.parse import quote

from app.enrichment.sources.base import LookupResult, SourceError, guarded, http_client, parse_json, status_result, tag_list

NAME = "virustotal"
TYPES = frozenset({"ip", "domain", "url", "file_hash"})
_API = "https://www.virustotal.com/api/v3"
_PATH = {"ip": "ip_addresses", "domain": "domains", "file_hash": "files", "url": "urls"}
_GUI = {"ip": "ip-address", "domain": "domain", "file_hash": "file", "url": "url"}


def _id(itype: str, value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=") if itype == "url" else quote(value, safe="")


async def _lookup(itype, value, key, transport):
    vid = _id(itype, value)
    async with http_client(transport) as c:
        resp = await c.get(f"{_API}/{_PATH[itype]}/{vid}", headers={"x-apikey": key})
    if resp.status_code == 404:
        return LookupResult(status="not_found")
    bad = status_result(NAME, resp)
    if bad:
        return bad
    data = parse_json(resp).get("data")
    if not isinstance(data, dict):
        raise SourceError(f"unexpected response from {NAME}")
    attrs = data.get("attributes") or {}
    st = attrs.get("last_analysis_stats") or {}
    mal, sus = int(st.get("malicious") or 0), int(st.get("suspicious") or 0)
    harmless, und = int(st.get("harmless") or 0), int(st.get("undetected") or 0)
    total = mal + sus + harmless + und
    if mal >= 3:
        verdict = "malicious"
    elif mal or sus:
        verdict = "suspicious"
    elif total:
        verdict = "harmless"
    else:
        verdict = "unknown"
    summary = {"malicious": mal, "suspicious": sus, "harmless": harmless, "undetected": und,
               "reputation": attrs.get("reputation"), "tags": tag_list(attrs.get("tags"))}
    label = (attrs.get("popular_threat_classification") or {}).get("suggested_threat_label")
    if label:
        summary["popular_threat_label"] = label
    return LookupResult(status="ok", verdict=verdict, score=f"{mal}/{total}" if total else None, summary=summary,
                        link=f"https://www.virustotal.com/gui/{_GUI[itype]}/{vid}")


async def lookup(itype: str, value: str, key: str, *, transport=None) -> LookupResult:
    return await guarded(NAME, _lookup(itype, value, key, transport))
