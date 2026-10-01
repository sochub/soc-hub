from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from app.enrichment.sources.base import LookupResult, SourceError, guarded, http_client, status_result

NAME = "crtsh"
TYPES = frozenset({"domain"})


async def _lookup(value, transport, now):
    q = quote(value, safe="")
    async with http_client(transport) as c:
        resp = await c.get(f"https://crt.sh/?q={q}&output=json")
    bad = status_result(NAME, resp)
    if bad:
        return bad
    try:
        certs = resp.json()
    except ValueError:
        raise SourceError("unexpected response from crt.sh") from None
    if not isinstance(certs, list):
        raise SourceError("unexpected response from crt.sh")
    if not certs:
        return LookupResult(status="not_found")
    first = min((c.get("not_before") for c in certs if c.get("not_before")), default=None)
    verdict = "unknown"
    if first:
        try:
            d = datetime.fromisoformat(first).replace(tzinfo=timezone.utc)
            verdict = "suspicious" if now - d < timedelta(days=30) else "unknown"
        except ValueError:
            pass
    return LookupResult(status="ok", verdict=verdict, summary={"cert_count": len(certs), "first_cert": first},
                        link=f"https://crt.sh/?q={q}")


async def lookup(itype, value, key=None, *, transport=None, now=None) -> LookupResult:
    return await guarded(NAME, _lookup(value, transport, now or datetime.now(timezone.utc)))
