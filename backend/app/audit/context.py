"""Per-request audit context, filled by auth dependencies and read by AuditMiddleware."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import Depends, Request

request_id_var: ContextVar[Optional[str]] = ContextVar("audit_request_id", default=None)


@dataclass
class AuditContext:
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    tenant_id: Optional[int] = None
    tenant_explicit: bool = False  # set by get_effective_tenant_id: the route really operates in tenant_id
    platform: bool = False  # set by require_super_admin: a platform-level route (no tenant unless explicit)
    actor: Optional[str] = None
    read_action: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)  # explicit audit_event rows, written by the middleware


def effective_tenant(ctx: AuditContext) -> Optional[int]:
    """Tenant a row/event of this request belongs to. Order-independent: an explicitly resolved tenant wins,
    a platform route without one is tenant-less, otherwise the provisional tenant from the token."""
    if ctx.tenant_explicit:
        return ctx.tenant_id
    if ctx.platform:
        return None
    return ctx.tenant_id


def ctx_of(request: Optional[Request]) -> Optional[AuditContext]:
    if request is None:
        return None
    return request.scope.get("state", {}).get("audit")


def audit_read(action: str):
    """Route dependency: record a successful GET on this route as a `read` row named `action`."""
    def mark(request: Request) -> None:
        ctx = ctx_of(request)
        if ctx is not None:
            ctx.read_action = action
    return Depends(mark)
