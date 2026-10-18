"""Explicit audit events (auth, credentials). Written in their own session so they survive a rollback."""
from typing import Any, Dict, Optional

from fastapi import Request

from app.audit.context import ctx_of, request_id_var
from app.audit.middleware import _route_template, insert_row
from app.audit.redact import scrub
from app.core.login_throttle import client_ip


async def audit_event(category: str, action: str, *, request: Optional[Request] = None,
                      tenant_id: Optional[int] = None, user_id: Optional[int] = None,
                      user_email: Optional[str] = None, actor: Optional[str] = None, outcome: str = "success",
                      entity_type: Optional[str] = None, entity_id: Optional[int] = None,
                      changes: Optional[Dict[str, Any]] = None) -> None:
    fields: Dict[str, Any] = dict(
        category=category, action=action, outcome=outcome, tenant_id=tenant_id, user_id=user_id,
        user_email=user_email, actor=actor, entity_type=entity_type, entity_id=entity_id,
        changes=scrub(changes), request_id=request_id_var.get(),
    )
    if request is not None:
        fields.update(method=request.method, route=_route_template(request.scope),
                      ip=(client_ip(request) or "")[:45],
                      user_agent=(request.headers.get("user-agent") or "")[:200] or None)
        ctx = ctx_of(request)
        if ctx is not None:
            ctx.skip = True  # this event is the record of the request; no duplicate write/denied row
    await insert_row(**fields)
