from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Case
from app.models.case_triage import CaseTriageResult
from app.models.user import User
from app.schemas.case_triage import CaseTriageOut, TriageItemUpdate
from app.tasks.triage import run_case_triage_task

router = APIRouter()

_FIELD_STATUS_COLUMN = {
    "severity_tags": "severity_tags_status",
    "playbook": "playbook_status",
    "next_steps": "next_steps_status",
}


async def _get_case(db: AsyncSession, case_id: int, tenant_id: int) -> Case:
    res = await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))
    case = res.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.post("/{case_id}/triage", response_model=CaseTriageOut, status_code=202)
async def trigger_triage(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Kick off (or re-run) AI triage for a case. Returns immediately with a
    pending row; the Celery task fills it in asynchronously."""
    await _get_case(db, case_id, tenant_id)
    triage = CaseTriageResult(
        case_id=case_id, tenant_id=tenant_id, triggered_by="manual",
        triggered_by_user_id=current_user.id, status="pending",
    )
    db.add(triage)
    await db.commit()
    await db.refresh(triage)
    run_case_triage_task.delay(triage.id)
    return triage


@router.get("/{case_id}/triage/latest", response_model=CaseTriageOut)
async def get_latest_triage(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _get_case(db, case_id, tenant_id)
    res = await db.execute(
        select(CaseTriageResult)
        .where(CaseTriageResult.case_id == case_id, CaseTriageResult.tenant_id == tenant_id)
        .order_by(CaseTriageResult.created_at.desc())
        .limit(1)
    )
    triage = res.scalars().first()
    if not triage:
        raise HTTPException(status_code=404, detail="No triage run yet")
    return triage


@router.patch("/{case_id}/triage/{triage_id}", response_model=CaseTriageOut)
async def update_triage_item(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    triage_id: int,
    body: TriageItemUpdate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Record confirm/dismiss on one triage sub-item. For confirms, the
    frontend executes the underlying Copilot action first, then calls this to
    mark that sub-item's status."""
    await _get_case(db, case_id, tenant_id)
    column = _FIELD_STATUS_COLUMN.get(body.field)
    if not column or body.status not in ("confirmed", "dismissed"):
        raise HTTPException(status_code=400, detail="Invalid triage field/status.")

    res = await db.execute(
        select(CaseTriageResult).where(
            CaseTriageResult.id == triage_id,
            CaseTriageResult.case_id == case_id,
            CaseTriageResult.tenant_id == tenant_id,
        )
    )
    triage = res.scalars().first()
    if not triage:
        raise HTTPException(status_code=404, detail="Triage result not found")

    setattr(triage, column, body.status)
    await db.commit()
    await db.refresh(triage)
    return triage
