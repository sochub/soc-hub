"""Alert triage mutations shared by the HTTP API (and the MCP server).

All functions only flush; the caller commits (and emits workflow events after commit).
"""
import asyncio
import json
import re

from fastapi import HTTPException
from sqlalchemy import case as sql_case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Alert, Case
from app.models.membership import TenantMembership
from app.services.case_service import add_alert_artifacts
from app.utils.audit import create_audit_log
from app.utils.copilot_heuristics import detect_indicators

FIELD_KEY = re.compile(r"^[A-Za-z0-9_\-@$]{1,64}$")  # payload keys a field path can name (schemas.suppression.FIELD_RE)

_SEVERITY_RANK = sql_case(
    {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}, value=Alert.severity, else_=0)
# Alert list orderings (REST ``sort`` and the MCP list_alerts tool).
SORTS = {"-created_at": Alert.created_at.desc(), "created_at": Alert.created_at.asc(),
         "severity": _SEVERITY_RANK.desc(), "-severity": _SEVERITY_RANK.asc()}


# ---- list filters on alert fields (the same field/op vocabulary as suppression rules) ----

MAX_LIST_CONDITIONS = 10
FIELD_SCAN = 300       # recent alerts /alerts/fields samples
CIDR_SCAN = 20_000     # rows in_cidr checks in Python after the SQL conditions narrowed them

_COLUMNS = {"source": Alert.source, "title": Alert.title, "severity": Alert.severity}


def _field_text(field: str):
    """SQL text of ``source``/``title``/``severity`` or ``payload.a.b`` (NULL when absent). Lists and objects come
    back as their JSON text, so ``contains`` finds an item in a list such as rule groups."""
    if field in _COLUMNS:
        return _COLUMNS[field]
    return func.json_extract_path_text(Alert.payload, *field.split(".")[1:])


def condition_clause(c: dict):
    """One condition as a SQL clause, case-insensitive like suppression rules; None for ``in_cidr`` (Python-side).
    A missing value matches only ``not_equals``."""
    if c["op"] == "in_cidr":
        return None
    raw = _field_text(c["field"])
    text, v = func.lower(func.btrim(raw)), c["value"].strip().lower()
    return {
        "equals": text == v,
        "not_equals": or_(raw.is_(None), text != v),
        "contains": text.contains(v, autoescape=True),
        "starts_with": text.startswith(v, autoescape=True),
        "ends_with": text.endswith(v, autoescape=True),
    }[c["op"]]


async def apply_conditions(db: AsyncSession, q, conditions: list):
    """Narrow an alerts ``select`` by every condition. SQL handles all but ``in_cidr``, which parses IPs in Python
    over at most CIDR_SCAN of the rows the other conditions left (newest first)."""
    from app.services.suppression_service import matches  # one in_cidr implementation for rules and filters

    for c in conditions:
        clause = condition_clause(c)
        if clause is not None:
            q = q.where(clause)
    cidr = [c for c in conditions if c["op"] == "in_cidr"]
    if cidr:
        rows = (await db.execute(q.with_only_columns(Alert.id, Alert.source, Alert.title, Alert.severity, Alert.payload)
                                 .order_by(Alert.id.desc()).limit(CIDR_SCAN))).all()
        keep = [r.id for r in rows if matches(cidr, {"source": r.source, "title": r.title, "severity": r.severity,
                                                     "payload": r.payload or {}})]
        q = q.where(Alert.id.in_(keep))
    return q


def _flatten(value, prefix: str, out: dict, depth: int = 0) -> None:
    if isinstance(value, dict) and depth < 8:
        for k, v in value.items():
            if isinstance(k, str) and FIELD_KEY.match(k):
                _flatten(v, f"{prefix}.{k}", out, depth + 1)
        return
    if depth == 0:
        return
    slot = out.setdefault(prefix, {"count": 0, "examples": []})
    slot["count"] += 1
    if not isinstance(value, (dict, list)) and value is not None:
        sample = str(value)[:80]
        if sample and sample not in slot["examples"] and len(slot["examples"]) < 3:
            slot["examples"].append(sample)


async def payload_fields(db: AsyncSession, tenant_id: int, source: str | None = None) -> list:
    """Field paths seen in the tenant's recent alert payloads (``payload.a.b``), most common first, with up to three
    example values each. Lists are offered as one field (match them with ``contains``)."""
    q = select(Alert.payload).where(Alert.tenant_id == tenant_id)
    if source:
        q = q.where(Alert.source == source)
    seen: dict = {}
    for payload in (await db.execute(q.order_by(Alert.id.desc()).limit(FIELD_SCAN))).scalars():
        _flatten(payload or {}, "payload", seen)
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:200]
    return [{"field": f, **v} for f, v in ranked]


SIMILAR_SCAN_CHARS = 8_000  # per compared payload: 500 alerts x 8 KB of regex work, off the event loop


def _payload_indicators(payload, cap=None) -> set:
    text = json.dumps(payload or {}, default=str)
    return {i["value"] for i in detect_indicators(text[:cap] if cap else text, limit=20)}


def indicators(alert: Alert) -> set:
    return _payload_indicators(alert.payload)


async def similar_alerts(db: AsyncSession, *, alert: Alert, tenant_id: int, limit: int = 10) -> list:
    """Other tenant alerts sharing at least one indicator (ip/domain/hash/...) with this one."""
    mine = await asyncio.to_thread(_payload_indicators, alert.payload)
    if not mine:
        return []
    # ponytail: in-memory compare over the newest 500 alerts (first SIMILAR_SCAN_CHARS of each payload); add an
    # alert_indicators table if tenants exceed that.
    others = (await db.execute(select(Alert).where(Alert.tenant_id == tenant_id, Alert.id != alert.id)
                               .order_by(Alert.created_at.desc()).limit(500))).scalars().all()
    payloads = [a.payload for a in others]  # read ORM attributes here, not in the worker thread
    hits = await asyncio.to_thread(
        lambda: [i for i, p in enumerate(payloads) if mine & _payload_indicators(p, SIMILAR_SCAN_CHARS)][:limit])
    return [others[i] for i in hits]


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
