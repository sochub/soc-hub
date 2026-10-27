"""Alert suppression: tenant rules matched at ingest. A match stores the alert as dismissed (never drops it),
records which rule did it, and skips the ``alert.ingested`` event, so suppressed noise starts no workflows."""
import ipaddress
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Alert
from app.models.suppression_rule import SuppressionRule
from app.utils.audit import create_audit_log

PREVIEW_SCAN = 500  # recent alerts a preview checks


def field_value(alert: dict, field: str) -> Any:
    """``source``/``title``/``severity`` or ``payload.a.b`` (list items by numeric index); None when absent."""
    if not field.startswith("payload."):
        return alert.get(field)
    cur: Any = alert.get("payload")
    for part in field.split(".")[1:]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _match(actual: Any, op: str, expected: str) -> bool:
    if actual is None or isinstance(actual, (dict, list)):
        return op == "not_equals"  # a missing or structured value equals nothing
    a, e = str(actual).strip().lower(), expected.strip().lower()
    if op == "equals":
        return a == e
    if op == "not_equals":
        return a != e
    if op == "contains":
        return e in a
    if op == "starts_with":
        return a.startswith(e)
    if op == "ends_with":
        return a.endswith(e)
    if op == "in_cidr":
        try:
            return ipaddress.ip_address(a) in ipaddress.ip_network(e, strict=False)
        except ValueError:
            return False
    return False


def matches(conditions: list, alert: dict) -> bool:
    """True when every condition holds (case-insensitive string compare; in_cidr parses IPs)."""
    return bool(conditions) and all(_match(field_value(alert, c["field"]), c["op"], c["value"]) for c in conditions)


def _alert_dict(a: Alert) -> dict:
    return {"source": a.source, "title": a.title, "severity": a.severity, "payload": a.payload or {}}


async def active_rules(db: AsyncSession, tenant_id: int) -> list:
    now = datetime.now(timezone.utc)
    return (await db.execute(select(SuppressionRule).where(
        SuppressionRule.tenant_id == tenant_id, SuppressionRule.enabled.is_(True),
        or_(SuppressionRule.expires_at.is_(None), SuppressionRule.expires_at > now),
    ).order_by(SuppressionRule.id))).scalars().all()


async def apply(db: AsyncSession, alert: Alert) -> Optional[SuppressionRule]:
    """Dismiss a freshly ingested, still-pending alert when an active rule matches. Flushes; the caller commits."""
    if alert.status != "pending":
        return None
    data = _alert_dict(alert)
    rule = next((r for r in await active_rules(db, alert.tenant_id) if matches(r.conditions, data)), None)
    if rule is None:
        return None
    alert.status = "dismissed"
    alert.suppression_rule_id = rule.id
    alert.dismiss_reason = f"Suppressed by rule #{rule.id}: {rule.name}"
    await db.execute(update(SuppressionRule).where(SuppressionRule.id == rule.id).values(
        hit_count=SuppressionRule.hit_count + 1, last_hit_at=datetime.now(timezone.utc)))
    await create_audit_log(db=db, entity_type="alert", entity_id=alert.id, action="alert_suppressed",
                           tenant_id=alert.tenant_id, user_id=None, actor="suppression",
                           changes={"rule_id": rule.id, "rule": rule.name})
    await db.flush()
    return rule


async def preview(db: AsyncSession, tenant_id: int, conditions: list) -> dict:
    """How many of the tenant's recent alerts the conditions would have matched, with a small sample."""
    recent = (await db.execute(select(Alert).where(Alert.tenant_id == tenant_id)
                               .order_by(Alert.id.desc()).limit(PREVIEW_SCAN))).scalars().all()
    hits = [a for a in recent if matches(conditions, _alert_dict(a))]
    return {"scanned": len(recent), "matched": len(hits),
            "sample": [{"id": a.id, "title": a.title, "source": a.source, "status": a.status} for a in hits[:10]]}


async def get_rule(db: AsyncSession, tenant_id: int, rule_id: int) -> SuppressionRule:
    rule = (await db.execute(select(SuppressionRule).where(
        SuppressionRule.id == rule_id, SuppressionRule.tenant_id == tenant_id))).scalars().first()
    if rule is None:
        raise HTTPException(status_code=404, detail="Suppression rule not found")
    return rule


async def create_rule(db: AsyncSession, *, tenant_id: int, user_id: Optional[int], data: dict) -> SuppressionRule:
    """``data`` is ``SuppressionCreate.model_dump()``. Flushes and audits; the caller commits."""
    if data.get("created_from_alert_id") is not None:
        owned = (await db.execute(select(Alert.id).where(
            Alert.id == data["created_from_alert_id"], Alert.tenant_id == tenant_id))).first()
        if not owned:
            raise HTTPException(status_code=404, detail="Alert not found")
    rule = SuppressionRule(tenant_id=tenant_id, created_by=user_id, **data)
    db.add(rule)
    await db.flush()
    await create_audit_log(db=db, entity_type="suppression_rule", entity_id=rule.id, action="create",
                           tenant_id=tenant_id, user_id=user_id,
                           changes={"name": rule.name, "conditions": rule.conditions, "enabled": rule.enabled,
                                    "expires_at": rule.expires_at.isoformat() if rule.expires_at else None})
    return rule


async def update_rule(db: AsyncSession, *, rule: SuppressionRule, user_id: Optional[int], data: dict) -> SuppressionRule:
    """``data`` is ``SuppressionUpdate.model_dump(exclude_unset=True)``. A used rule keeps its conditions."""
    if "conditions" in data and data["conditions"] != rule.conditions and rule.hit_count:
        raise HTTPException(status_code=409, detail="This rule has suppressed alerts, so its conditions can't change. "
                                                    "Disable it and create a new one.")
    changes = {}
    for field, value in data.items():
        old = getattr(rule, field)
        if old != value:
            changes[field] = {"from": old.isoformat() if isinstance(old, datetime) else old,
                              "to": value.isoformat() if isinstance(value, datetime) else value}
            setattr(rule, field, value)
    if changes:
        await create_audit_log(db=db, entity_type="suppression_rule", entity_id=rule.id, action="update",
                               tenant_id=rule.tenant_id, user_id=user_id, changes=changes)
    await db.flush()
    return rule


async def delete_rule(db: AsyncSession, *, rule: SuppressionRule, user_id: Optional[int]) -> None:
    if rule.hit_count:
        raise HTTPException(status_code=409, detail="This rule has suppressed alerts, so it can only be disabled.")
    await create_audit_log(db=db, entity_type="suppression_rule", entity_id=rule.id, action="delete",
                           tenant_id=rule.tenant_id, user_id=user_id,
                           changes={"name": rule.name, "conditions": rule.conditions})
    await db.delete(rule)
    await db.flush()
