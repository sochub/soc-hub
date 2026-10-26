"""Alert triage mutations shared by the HTTP API (and the MCP server).

All functions only flush; the caller commits (and emits workflow events after commit).
"""
import json

from fastapi import HTTPException
from sqlalchemy import case as sql_case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Alert, Case
from app.models.membership import TenantMembership
from app.services.case_service import add_alert_artifacts
from app.utils.audit import create_audit_log
from app.utils.copilot_heuristics import detect_indicators

_SEVERITY_RANK = sql_case(
    {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}, value=Alert.severity, else_=0)
# Alert list orderings (REST ``sort`` and the MCP list_alerts tool).
SORTS = {"-created_at": Alert.created_at.desc(), "created_at": Alert.created_at.asc(),
         "severity": _SEVERITY_RANK.desc(), "-severity": _SEVERITY_RANK.asc()}


def indicators(alert: Alert) -> set:
    return {i["value"] for i in detect_indicators(json.dumps(alert.payload or {}, default=str), limit=20)}


async def similar_alerts(db: AsyncSession, *, alert: Alert, tenant_id: int, limit: int = 10) -> list:
    """Other tenant alerts sharing at least one indicator (ip/domain/hash/...) with this one."""
    mine = indicators(alert)
    if not mine:
        return []
    # ponytail: in-memory compare over the newest 500 alerts; add an alert_indicators table if tenants exceed that.
    others = (await db.execute(select(Alert).where(Alert.tenant_id == tenant_id, Alert.id != alert.id)
                               .order_by(Alert.created_at.desc()).limit(500))).scalars().all()
    return [a for a in others if mine & indicators(a)][:limit]


async def is_tenant_member(db: AsyncSession, user_id: int, tenant_id: int) -> bool:
    return (await db.execute(select(TenantMembership.id).where(
        TenantMembership.user_id == user_id, TenantMembership.tenant_id == tenant_id))).first() is not None


async def update_alert(db: AsyncSession, *, alert: Alert, data: dict, tenant_id: int, user_id: int) -> dict:
    """Set severity and/or owner. Raises 400 when the owner is not a member of the tenant."""
    changes = dict(data)
    if changes.get("severity") is None:
        changes.pop("severity", None)  # severity is NOT NULL; null means "leave as is"
    if changes.get("owner_id") is not None and not await is_tenant_member(db, changes["owner_id"], tenant_id):
        raise HTTPException(status_code=400, detail="Owner is not a member of this tenant")
    for k, v in changes.items():
        setattr(alert, k, v)
    await create_audit_log(db=db, entity_type="alert", entity_id=alert.id, action="alert_updated",
                           tenant_id=tenant_id, user_id=user_id, changes=changes)
    await db.flush()
    return changes


async def dismiss_alert(db: AsyncSession, *, alert: Alert, user_id: int, reason: str | None = None,
                        bulk: bool = False) -> None:
    """Dismiss; without a reason an existing one is kept (re-dismissing never erases it)."""
    alert.status = "dismissed"
    if reason is not None:
        alert.dismiss_reason = reason
    changes = {"reason": reason, "bulk": True} if bulk else {"reason": reason}
    await create_audit_log(db=db, entity_type="alert", entity_id=alert.id, action="alert_dismissed",
                           tenant_id=alert.tenant_id, user_id=user_id, changes=changes)
    await db.flush()


async def promote_alert(db: AsyncSession, *, alert: Alert, case: Case, user_id: int) -> None:
    """Link the alert to the case and copy its indicators onto the case as artifacts."""
    alert.case_id = case.id
    alert.status = "promoted"
    await add_alert_artifacts(db, case=case, alert=alert, user_id=user_id)
    await create_audit_log(db=db, entity_type="alert", entity_id=alert.id, action="alert_promoted",
                           tenant_id=alert.tenant_id, user_id=user_id, changes={"case_id": case.id})
    await db.flush()
