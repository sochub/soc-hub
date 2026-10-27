import json
import zlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.models.case import Alert, Case, CaseSeverity, CaseStatus
from app.services import suppression_service
from app.services.case_service import add_alert_artifacts, create_case_record
from app.utils.audit import create_audit_log
from app.workflows.events import emit_event
from app.workflows.nodes import NodeContext, NodeError, executor
from app.workflows.templating import eval_expr


def _case_data(config: dict) -> dict:
    tags = config.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    try:
        severity = CaseSeverity(config.get("severity") or "medium")
    except ValueError:
        raise NodeError(f"invalid severity {config.get('severity')!r}")
    return {"title": str(config["title"]), "description": config.get("description") or "",
            "severity": severity, "tags": tags, "source": "automation"}


async def find_or_create_grouped_case(db, *, tenant_id: int, group_key: str, window_hours: float, case_data: dict):
    lock_key = zlib.crc32(f"{tenant_id}:{group_key}".encode())
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    existing = (await db.execute(
        select(Case).where(Case.tenant_id == tenant_id, Case.group_key == group_key, Case.created_at >= since,
                           Case.status.notin_([CaseStatus.RESOLVED, CaseStatus.CLOSED]))
        .order_by(Case.created_at.desc()).limit(1)
    )).scalars().first()
    if existing:
        return existing, False, None
    data = dict(case_data)
    if isinstance(data.get("severity"), str):
        data["severity"] = CaseSeverity(data["severity"])
    case, triage_id = await create_case_record(db, tenant_id=tenant_id, data={**data, "group_key": group_key}, user_id=None)
    return case, True, triage_id


async def _load_pending_alert(nctx: NodeContext) -> Alert:
    alert = (await nctx.db.execute(select(Alert).where(
        Alert.id == nctx.run.alert_id, Alert.tenant_id == nctx.run.tenant_id).with_for_update()
        .execution_options(populate_existing=True))).scalars().first()
    if not alert:
        raise NodeError("this run has no alert")
    if alert.status != "pending":
        raise NodeError(f"alert {alert.id} is already {alert.status}")
    return alert


@executor("alert_promote")
async def run_alert_promote(nctx: NodeContext, config: dict) -> dict:
    from app.tasks.triage import run_case_triage_task
    mode = config["mode"]
    if mode == "group" and not str(config.get("group_key") or "").strip():
        raise NodeError("group_key rendered empty — check the template")
    alert = await _load_pending_alert(nctx)
    triage_id = None
    if mode == "link":
        try:
            cid = int(config["case_id"])
        except (TypeError, ValueError):
            raise NodeError(f"case_id must be an integer, got {config.get('case_id')!r}")
        case = (await nctx.db.execute(select(Case).where(Case.id == cid, Case.tenant_id == nctx.run.tenant_id))).scalars().first()
        if not case:
            raise NodeError(f"case {cid} not found")
        created = False
    elif mode == "group":
        case, created, triage_id = await find_or_create_grouped_case(
            nctx.db, tenant_id=nctx.run.tenant_id, group_key=str(config["group_key"]),
            window_hours=float(config.get("window_hours") or 24), case_data=_case_data(config))
    else:
        case, triage_id = await create_case_record(nctx.db, tenant_id=nctx.run.tenant_id, data=_case_data(config), user_id=None)
        created = True

    alert.status = "promoted"
    alert.case_id = case.id
    nctx.run.case_id = case.id  # later nodes now act on this case
    await add_alert_artifacts(nctx.db, case=case, alert=alert, user_id=None)
    await create_audit_log(db=nctx.db, entity_type="alert", entity_id=alert.id, action="promote",
                           tenant_id=nctx.run.tenant_id, user_id=None,
                           changes={"case_id": case.id, "mode": mode, "by": "automation"})
    await nctx.db.flush()

    run = nctx.run
    if created:
        nctx.after_commit.append(lambda: run_case_triage_task.delay(triage_id))
        nctx.after_commit.append(lambda: emit_event(run.tenant_id, "case.created", case_id=case.id, depth=run.depth + 1))
    return {"case_id": case.id, "created": created}


@executor("alert_dismiss")
async def run_alert_dismiss(nctx: NodeContext, config: dict) -> dict:
    alert = await _load_pending_alert(nctx)
    alert.status = "dismissed"
    alert.dismiss_reason = config.get("reason") or None
    await create_audit_log(db=nctx.db, entity_type="alert", entity_id=alert.id, action="dismiss",
                           tenant_id=nctx.run.tenant_id, user_id=None,
                           changes={"reason": alert.dismiss_reason, "by": "automation"})
    return {"alert_id": alert.id, "dismissed": True}


_ALERT_SEVERITIES = ("critical", "high", "medium", "low", "info")
MAX_ALERT_PAYLOAD = 256 * 1024


@executor("alert_create")
async def run_alert_create(nctx: NodeContext, config: dict) -> dict:
    """Pull-style ingest: same idempotent insert as the webhook (alerts.ingest_alert)."""
    source = str(config.get("source") or "").strip()
    external_id = str(config.get("external_id") or "").strip()
    if not 1 <= len(source) <= 100:
        raise NodeError("source must be 1-100 characters")
    if not 1 <= len(external_id) <= 255:
        raise NodeError("external_id must be 1-255 characters")
    title = str(config.get("title") or "")[:500]
    raw_sev = str(config.get("severity") or "").strip().lower()
    severity = raw_sev if raw_sev in _ALERT_SEVERITIES else "medium"
    payload = config.get("payload")
    if isinstance(payload, str):
        payload = eval_expr(payload, nctx.ctx) if payload.strip() else {}
    elif payload is None:
        payload = {}
    if not isinstance(payload, (dict, list)):
        raise NodeError(f"payload must evaluate to an object or list, got {type(payload).__name__}")
    try:
        size = len(json.dumps(payload))
    except (TypeError, ValueError):
        raise NodeError("payload is not JSON-serialisable") from None
    if size > MAX_ALERT_PAYLOAD:
        raise NodeError(f"payload is {size} bytes; the limit is {MAX_ALERT_PAYLOAD}")

    tenant_id = nctx.run.tenant_id
    alert_id = (await nctx.db.execute(
        insert(Alert).values(source=source, external_id=external_id, title=title, payload=payload,
                             status="pending", severity=severity, tenant_id=tenant_id)
        .on_conflict_do_nothing(constraint="uq_alerts_tenant_source_external").returning(Alert.id)
    )).scalar()
    created = alert_id is not None
    suppressed = None
    if created:
        await create_audit_log(db=nctx.db, entity_type="alert", entity_id=alert_id, action="created_by_workflow",
                               tenant_id=tenant_id, user_id=None,
                               changes={"source": source, "external_id": external_id, "by": "automation"})
        alert = (await nctx.db.execute(select(Alert).where(Alert.id == alert_id))).scalars().one()
        suppressed = await suppression_service.apply(nctx.db, alert)
        if suppressed is None:  # suppressed noise starts no workflows
            depth = nctx.run.depth + 1
            nctx.after_commit.append(lambda: emit_event(tenant_id, "alert.ingested", alert_id=alert_id, depth=depth))
    else:
        alert_id = (await nctx.db.execute(select(Alert.id).where(
            Alert.tenant_id == tenant_id, Alert.source == source, Alert.external_id == external_id))).scalar_one()
    out = {"alert_id": alert_id, "created": created}
    if suppressed is not None:
        out["suppressed_by_rule"] = suppressed.id
    if raw_sev and raw_sev not in _ALERT_SEVERITIES:
        out["severity_defaulted"] = True
    return out
