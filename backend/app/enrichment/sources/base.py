"""Shared pieces for enrichment sources."""
from dataclasses import dataclass, field
from typing import Optional

import httpx


class SourceError(Exception):
    pass


@dataclass
class LookupResult:
    status: str
    verdict: Optional[str] = None
    score: Optional[str] = None
    summary: dict = field(default_factory=dict)
    link: Optional[str] = None
    error: Optional[str] = None


def http_client(transport=None, follow_redirects: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0, follow_redirects=follow_redirects, transport=transport,
                             headers={"User-Agent": "SOC-Hub-Enrichment/1.0"})


def parse_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise SourceError(f"unexpected response from {resp.request.url.host}") from None
    if not isinstance(data, dict):
        raise SourceError(f"unexpected response from {resp.request.url.host}")
    return data


def status_result(name: str, resp: httpx.Response) -> Optional[LookupResult]:
    if resp.status_code in (401, 403):
        return LookupResult(status="error", error="invalid API key")
    if resp.status_code == 429:
        return LookupResult(status="rate_limited")
    if resp.status_code >= 500:
        return LookupResult(status="error", error=f"{name} unavailable (HTTP {resp.status_code})")
    return None


async def guarded(name: str, coro) -> LookupResult:
    """Run a source request coroutine, mapping transport failures to error results."""
    try:
        return await coro
    except httpx.TimeoutException:
        return LookupResult(status="error", error=f"{name} timed out")
    except SourceError as e:
        return LookupResult(status="error", error=str(e))
    except httpx.HTTPError as e:
        return LookupResult(status="error", error=f"{name} request failed ({type(e).__name__})")
