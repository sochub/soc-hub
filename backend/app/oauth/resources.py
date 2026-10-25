"""Registry of protected resources an OAuth token can be audience-bound to."""
from dataclasses import dataclass
from typing import Callable, Optional

from app.core.config import settings
from app.models.tenant import Tenant


def issuer() -> str:
    return settings.PUBLIC_BASE_URL.rstrip("/")


@dataclass(frozen=True)
class Resource:
    key: str
    url: str
    scopes: tuple[str, ...]
    tenant_gate: Callable[[Tenant], bool]


RESOURCES: dict[str, Resource] = {
    "mcp": Resource("mcp", f"{settings.PUBLIC_BASE_URL}/mcp", ("mcp",), lambda t: t.mcp_enabled),
}


def resource_by_url(url: Optional[str]) -> Optional[Resource]:
    """None / omitted selects the default (mcp); an unknown URL returns None."""
    if not url:
        return RESOURCES["mcp"]
    return next((r for r in RESOURCES.values() if r.url == url), None)
