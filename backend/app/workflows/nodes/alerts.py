import zlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.models.case import Alert, Case, CaseSeverity, CaseStatus
from app.services.case_service import create_case_record
from app.utils.audit import create_audit_log
from app.workflows.events import emit_event
from app.workflows.nodes import NodeContext, NodeError, executor


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
        Alert.id == nctx.run.alert_id, Alert.tenant_id == nctx.run.tenant_id).with_for_update())).scalars().first()
    if not alert:
        raise NodeError("this run has no alert")
    if alert.status != "pending":
        raise NodeError(f"alert {alert.id} is already {alert.status}")
    return alert


@executor("alert_promote")
async def run_alert_promote(nctx: NodeContext, config: dict) -> dict:
    from app.tasks.triage import run_case_triage_task
    alert = await _load_pending_alert(nctx)
    mode = config["mode"]
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
