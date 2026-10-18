"""Per-request audit context, filled by auth dependencies and read by AuditMiddleware."""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Request

request_id_var: ContextVar[Optional[str]] = ContextVar("audit_request_id", default=None)


@dataclass
class AuditContext:
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    tenant_id: Optional[int] = None
    actor: Optional[str] = None
    read_action: Optional[str] = None
    skip: bool = False  # an explicit audit_event already covers this request


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
