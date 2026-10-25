import json
from typing import Any, List, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import case as sql_case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.models.case import Alert, Case
from app.models.tenant import Tenant
from app.models.user import User
from app.models.webhook import Webhook
from app.schemas import case as case_schema
from app.services import alert_service
from app.utils.copilot_heuristics import detect_indicators
from app.workflows.events import emit_event

router = APIRouter()

@router.post("/webhook", response_model=case_schema.Alert)
async def ingest_alert(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_in: case_schema.AlertWebhookCreate,
    webhook: Webhook = Depends(deps.get_webhook_from_key),
) -> Any:
    """Ingest an alert via a tenant webhook.

    The webhook's API key determines both the destination tenant and the alert
    ``source`` (the webhook's name), so source attribution is authoritative and
    not caller-controlled.

    Idempotent on ``(tenant, source, external_id)``: re-posting an alert that
    already exists returns the existing row and does not re-emit
    ``alert.ingested``. ``ON CONFLICT DO NOTHING`` makes concurrent duplicates
    safe — exactly one insert wins.
    """
    inserted_id = (await db.execute(
        insert(Alert)
        .values(
            source=webhook.name,
            external_id=alert_in.external_id,
            title=alert_in.title,
            payload=alert_in.payload,
            status="pending",
            severity=alert_in.severity or "medium",
            tenant_id=webhook.tenant_id,
        )
        .on_conflict_do_nothing(constraint="uq_alerts_tenant_source_external")
        .returning(Alert.id)
    )).scalar()
    await db.commit()
    if inserted_id is not None:
        alert = (await db.execute(select(Alert).where(Alert.id == inserted_id))).scalars().one()
        emit_event(webhook.tenant_id, "alert.ingested", alert_id=alert.id)
        return alert
    # Duplicate: return the existing alert for this natural key.
    return (await db.execute(
        select(Alert).where(
            Alert.tenant_id == webhook.tenant_id,
            Alert.source == webhook.name,
            Alert.external_id == alert_in.external_id,
        )
    )).scalars().one()

_SEVERITY_RANK = sql_case(
    {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}, value=Alert.severity, else_=0)
_SORTS = {"-created_at": Alert.created_at.desc(), "created_at": Alert.created_at.asc(),
          "severity": _SEVERITY_RANK.desc(), "-severity": _SEVERITY_RANK.asc()}


@router.get("/", response_model=List[case_schema.Alert])
async def read_alerts(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    status: Optional[str] = None,
    severity: Optional[str] = None,
    source: Optional[str] = None,
    sort: str = Query("-created_at", pattern="^(-?created_at|-?severity)$"),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Retrieve alerts scoped to tenant, optionally filtered and sorted."""
    q = select(Alert).where(Alert.tenant_id == tenant_id)
    for col, val in ((Alert.status, status), (Alert.severity, severity), (Alert.source, source)):
        if val:
            q = q.where(col == val)
    result = await db.execute(q.order_by(_SORTS[sort], Alert.id.desc()).offset(skip).limit(limit))
    return result.scalars().all()


async def _get_alert(db: AsyncSession, alert_id: int, tenant_id: int) -> Alert:
    alert = (await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.tenant_id == tenant_id))).scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


def _indicators(alert: Alert) -> set:
    return {i["value"] for i in detect_indicators(json.dumps(alert.payload or {}, default=str), limit=20)}


@router.patch("/{alert_id}", response_model=case_schema.Alert)
async def update_alert(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_id: int,
    body: case_schema.AlertUpdate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Set an alert's severity and/or owner (owner must be a member of the tenant)."""
    alert = await _get_alert(db, alert_id, tenant_id)
    await alert_service.update_alert(db, alert=alert, data=body.model_dump(exclude_unset=True),
                                     tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    await db.refresh(alert)
    return alert


@router.get("/{alert_id}/similar", response_model=List[case_schema.Alert])
async def similar_alerts(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Other tenant alerts sharing at least one indicator (ip/domain/hash/...) with this one."""
    alert = await _get_alert(db, alert_id, tenant_id)
    mine = _indicators(alert)
    if not mine:
        return []
    # ponytail: in-memory compare over the newest 500 alerts; add an alert_indicators table if tenants exceed that.
    others = (await db.execute(select(Alert).where(Alert.tenant_id == tenant_id, Alert.id != alert.id)
                               .order_by(Alert.created_at.desc()).limit(500))).scalars().all()
    return [a for a in others if mine & _indicators(a)][:10]


@router.post("/bulk")
async def bulk_alerts(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: case_schema.AlertBulk,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Dismiss or promote many alerts at once. Ids outside the tenant are skipped, never touched."""
    case = None
    if body.action == "promote":
        case = (await db.execute(select(Case).where(
            Case.id == body.case_id, Case.tenant_id == tenant_id))).scalars().first() if body.case_id else None
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
    alerts = (await db.execute(select(Alert).where(
        Alert.id.in_(body.ids), Alert.tenant_id == tenant_id))).scalars().all()
    for alert in alerts:
        if case:
            await alert_service.promote_alert(db, alert=alert, case=case, user_id=current_user.id)
        else:
            await alert_service.dismiss_alert(db, alert=alert, user_id=current_user.id)
    await db.commit()
    found = {a.id for a in alerts}
    ids = list(dict.fromkeys(body.ids))
    return {"updated": [i for i in ids if i in found], "skipped": [i for i in ids if i not in found]}


@router.post("/{alert_id}/promote/{case_id}", response_model=case_schema.Alert)
async def promote_alert_to_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_id: int,
    case_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Link an alert to a case (promote). Both must belong to tenant."""
    alert = await _get_alert(db, alert_id, tenant_id)
    result_case = await db.execute(
        select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id)
    )
    case = result_case.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    await alert_service.promote_alert(db, alert=alert, case=case, user_id=current_user.id)
    await db.commit()
    await db.refresh(alert)
    return alert

@router.post("/{alert_id}/dismiss", response_model=case_schema.Alert)
async def dismiss_alert(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_id: int,
    body: Optional[case_schema.AlertDismiss] = Body(None),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Mark an alert as dismissed, with an optional reason. Tenant-scoped."""
    alert = await _get_alert(db, alert_id, tenant_id)
    await alert_service.dismiss_alert(db, alert=alert, user_id=current_user.id, reason=body.reason if body else None)
    await db.commit()
    await db.refresh(alert)
    return alert
