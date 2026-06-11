from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas import audit_log as audit_log_schema

router = APIRouter()

@router.get("/{entity_type}/{entity_id}", response_model=List[audit_log_schema.AuditLog])
async def read_audit_logs(
    *,
    db: AsyncSession = Depends(deps.get_db),
    entity_type: str,
    entity_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Retrieve audit logs for a specific entity (tenant-scoped)."""
    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == entity_id,
            AuditLog.tenant_id == tenant_id,
        )
        .order_by(AuditLog.created_at.desc())
    )
    return result.scalars().all()
