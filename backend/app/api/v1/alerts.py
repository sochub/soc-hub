from typing import Any, List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.models.case import Alert, Case
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas import case as case_schema

router = APIRouter()

@router.post("/webhook", response_model=case_schema.Alert)
async def ingest_alert(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_in: case_schema.AlertCreate,
    tenant: Tenant = Depends(deps.get_tenant_from_webhook_key),
) -> Any:
    """
    Ingest a new alert from an external source.

    Authenticated with the tenant's own ``X-API-Key``. The key alone determines
    the destination tenant — there is no caller-supplied tenant header, so a
    key can only ever write alerts into the tenant that owns it.
    """
    alert = Alert(**alert_in.model_dump(), tenant_id=tenant.id)
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert

@router.get("/", response_model=List[case_schema.Alert])
async def read_alerts(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Retrieve alerts scoped to tenant."""
    result = await db.execute(
        select(Alert)
        .where(Alert.tenant_id == tenant_id)
        .offset(skip).limit(limit)
        .order_by(Alert.created_at.desc())
    )
    return result.scalars().all()

@router.post("/{alert_id}/promote/{case_id}", response_model=case_schema.Alert)
async def promote_alert_to_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_id: int,
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Link an alert to a case (promote). Both must belong to tenant."""
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.tenant_id == tenant_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    result_case = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result_case.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    alert.case_id = case.id
    alert.status = "promoted"
    await db.commit()
    await db.refresh(alert)
    return alert
