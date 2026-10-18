"""Helper for detailed `change` audit rows (before/after). Request/security/read rows come from app.audit."""
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.context import request_id_var
from app.audit.redact import scrub
from app.models.audit_log import AuditLog


async def create_audit_log(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    action: str,
    tenant_id: int,
    user_id: Optional[int] = None,
    changes: Optional[Dict[str, Any]] = None,
    actor: Optional[str] = None,
) -> AuditLog:
    """Add a `change` row in the caller's transaction (the caller commits). `changes` is scrubbed."""
    audit_log = AuditLog(
        category="change", entity_type=entity_type, entity_id=entity_id, action=action, tenant_id=tenant_id,
        user_id=user_id, actor=actor, changes=scrub(changes), request_id=request_id_var.get(),
    )
    db.add(audit_log)
    await db.flush()
    return audit_log
