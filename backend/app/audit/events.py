"""Explicit audit events (auth, credentials).

With a request, the event is queued on the audit context and written by AuditMiddleware after the handler has
released its DB session (no second pooled connection while holding one; survives the request's rollback)."""
import logging
from typing import Any, Dict, Optional

from fastapi import Request

from app.audit.context import ctx_of, request_id_var
from app.audit.middleware import _route_template, audit_ip, insert_row
from app.audit.redact import scrub
from app.audit.via import apply_via

logger = logging.getLogger(__name__)
_FROM_CTX: Any = object()  # tenant_id not passed: resolve from the request's audit context (effective_tenant)


async def audit_event(category: str, action: str, *, request: Optional[Request] = None,
                      tenant_id: Optional[int] = _FROM_CTX, user_id: Optional[int] = None,
                      user_email: Optional[str] = None, actor: Optional[str] = None, outcome: str = "success",
                      entity_type: Optional[str] = None, entity_id: Optional[int] = None,
                      changes: Optional[Dict[str, Any]] = None) -> None:
    try:
        from_ctx = tenant_id is _FROM_CTX
        actor, changes = apply_via(actor, changes)  # captured now: queued rows are written after the context ends
        fields: Dict[str, Any] = dict(
            category=category, action=action, outcome=outcome, tenant_id=None if from_ctx else tenant_id,
            user_id=user_id,
            user_email=user_email, actor=actor, entity_type=entity_type, entity_id=entity_id,
            changes=scrub(changes), request_id=request_id_var.get(),
        )
        ctx = None
        if request is not None:
            fields.update(method=request.method, route=_route_template(request.scope),
                          ip=audit_ip(request),
                          user_agent=(request.headers.get("user-agent") or "")[:200] or None)
            ctx = ctx_of(request)
        if ctx is not None:
            if from_ctx:
                fields["_tenant_from_ctx"] = True  # resolved when written: later dependencies may refine it
            ctx.events.append(fields)
        else:
            await insert_row(**fields)
    except Exception as exc:  # auditing must never break an auth flow
        logger.error("audit_event failed action=%s err=%s", action, type(exc).__name__)
