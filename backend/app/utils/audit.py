"""Helper functions for audit logging"""
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit_log import AuditLog
from typing import Optional, Dict, Any

async def create_audit_log(
    db: AsyncSession,
    entity_type: str,
    entity_id: int,
    action: str,
    tenant_id: int,
    user_id: Optional[int] = None,
    changes: Optional[Dict[str, Any]] = None
) -> AuditLog:
    """
    Create an audit log entry.

    Args:
        db: Database session
        entity_type: Type of entity (e.g., 'case', 'artifact')
        entity_id: ID of the entity
        action: Action performed (create, update, delete)
        tenant_id: Tenant ID for row-level isolation
        user_id: ID of the user who performed the action
        changes: Dictionary of changes (for updates, store before/after)
    """
    audit_log = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        tenant_id=tenant_id,
        user_id=user_id,
        changes=changes
    )
    db.add(audit_log)
    await db.flush()  # Don't commit here, let the caller commit
    return audit_log
