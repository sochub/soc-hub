"""Incident report model: gathers a case into one plain dict (raw values, aware datetimes).

Escaping and defanging happen at render time; truncation is computed here but applied by the PDF renderer.
"""
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import and_, or_, select

from app.enrichment.indicators import normalise
from app.models.artifact import Artifact
from app.models.audit_log import AuditLog
from app.models.case import Alert, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_attachment import CaseAttachment
from app.models.case_report import CaseReport
from app.models.case_task import CaseTask
from app.models.enrichment_result import EnrichmentResult
from app.models.ioc import IOC
from app.models.user import User
from app.reports import milestones, tlp
from app.utils.sla import compute_sla_state, load_policy_overrides

SCHEMA = "sochub.incident-report/1"
APP_VERSION = "0.1.0"  # mirrors FastAPI(version=...) in app/main.py
TEXT_MAX = 300
ENRICH_BATCH = 1000  # max (type, value) pairs per enrichment lookup query
PDF_LIMITS = {"timeline": 500, "iocs": 1000, "evidence": 1000, "audit": 2000}
PHASES = ("identification", "containment", "eradication", "recovery", "lessons_learned")
_THREAT_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_LANES = {"comment": "analyst", "status_change": "analyst", "severity_change": "analyst",
          "artifact_added": "indicators", "artifact_removed": "indicators",
          "attachment_added": "evidence", "attachment_deleted": "evidence"}
_FULL_ONLY = {"artifact_removed"}  # R5: attachment_deleted stays in key mode (chain of custody)

aware = milestones.aware


def _val(x):
    return x.value if hasattr(x, "value") else x


async def report_row(db, case) -> Optional[CaseReport]:
    return (await db.execute(select(CaseReport).where(CaseReport.case_id == case.id,
                                                      CaseReport.tenant_id == case.tenant_id))).scalar_one_or_none()


async def min_tlp(db, case) -> str:
    rows = (await db.execute(select(IOC.tlp).where(IOC.case_id == case.id, IOC.tenant_id == case.tenant_id))).scalars()
    return tlp.floor(rows)


async def _emails(db, user_ids) -> Dict[int, str]:
    ids = {i for i in user_ids if i is not None}
    if not ids:
        return {}
    return dict((await db.execute(select(User.id, User.email).where(User.id.in_(ids)))).all())


def compute_truncated(report: dict) -> Dict[str, int]:
    out = {}
    for section, limit in PDF_LIMITS.items():
        extra = len(report.get(section) or []) - limit
        if extra > 0:
            out[section] = extra
    return out


async def build_report(db, case, *, full: bool, generated_by) -> dict:
    tid, cid = case.tenant_id, case.id
    row = await report_row(db, case)

    iocs = (await db.execute(select(IOC).where(IOC.case_id == cid, IOC.tenant_id == tid)
                             .order_by(IOC.id))).scalars().all()
    arts = (await db.execute(select(Artifact).join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
                             .where(CaseArtifact.case_id == cid, Artifact.tenant_id == tid)
                             .order_by(Artifact.id))).scalars().all()
    alerts = (await db.execute(select(Alert).where(Alert.case_id == cid, Alert.tenant_id == tid)
                               .order_by(Alert.id))).scalars().all()
    tasks = (await db.execute(select(CaseTask).where(CaseTask.case_id == cid, CaseTask.tenant_id == tid)
                              .order_by(CaseTask.order, CaseTask.id))).scalars().all()
    events = (await db.execute(select(TimelineEvent).where(TimelineEvent.case_id == cid)
                               .order_by(TimelineEvent.id))).scalars().all()
    attachments = (await db.execute(select(CaseAttachment).where(CaseAttachment.case_id == cid,
                                                                 CaseAttachment.tenant_id == tid)
                                    .order_by(CaseAttachment.id))).scalars().all()

    # --- indicators (case IOCs first, then enrichable artifacts), deduplicated by normalised pair ---
    # Keys are normalised pairs (dedupe + enrichment matching); displayed values are the stored raw value,
    # except URLs, which show the normalised form so userinfo (credentials) never reaches a report.
    entries: Dict[tuple, dict] = {}

    def _add(key, n, raw_type, raw_value, tlp_, threat):
        if key in entries:
            return
        shown = n[1] if n and n[0] == "url" else raw_value
        entries[key] = {"value": shown, "type": n[0] if n else raw_type, "tlp": tlp_, "threat_level": threat,
                        "verdicts": []}

    for i in iocs:
        n = normalise(i.ioc_type, i.value)
        _add(n or (i.ioc_type, i.value), n, i.ioc_type, i.value, i.tlp, i.threat_level)
    for a in arts:
        n = normalise(_val(a.artifact_type), a.value)
        if n:
            _add(n, n, None, a.value, None, None)
    enrich = []
    pairs = list(entries)
    for start in range(0, len(pairs), ENRICH_BATCH):
        batch = pairs[start:start + ENRICH_BATCH]
        enrich += (await db.execute(select(EnrichmentResult).where(
            EnrichmentResult.tenant_id == tid, EnrichmentResult.status == "ok",
            or_(*[and_(EnrichmentResult.indicator_type == t, EnrichmentResult.indicator_value == v)
                  for t, v in batch])))).scalars().all()
    enrich.sort(key=lambda r: (r.source, r.id))
    for r in enrich:
        entries[(r.indicator_type, r.indicator_value)]["verdicts"].append(
            {"source": r.source, "verdict": r.verdict, "score": r.score})
    ioc_list = sorted(entries.values(), key=lambda e: (
        not any(v["verdict"] == "malicious" for v in e["verdicts"]),
        _THREAT_RANK.get((e["threat_level"] or "").lower(), len(_THREAT_RANK)),
        e["value"]))

    emails = await _emails(db, [case.owner_id, generated_by.id if generated_by else None,
                                row.updated_by if row else None,
                                *(e.user_id for e in events), *(t.completed_by for t in tasks),
                                *(a.uploaded_by for a in attachments)])

    # --- timeline ---
    tl = [{"at": aware(case.created_at), "lane": "detection", "kind": "case_created", "text": "Case created",
           "actor": None}]
    for a in alerts:
        if a.created_at is not None:
            tl.append({"at": aware(a.created_at), "lane": "detection", "kind": "alert",
                       "text": f"Alert received: {a.title or ''}", "actor": a.source})
    for e in events:
        if e.created_at is None or (not full and e.event_type in _FULL_ONLY):
            continue
        tl.append({"at": aware(e.created_at), "lane": _LANES.get(e.event_type, "analyst"), "kind": e.event_type,
                   "text": (e.content or "")[:TEXT_MAX], "actor": emails.get(e.user_id)})
    done = [t for t in tasks if t.status == "done"]
    for t in done:
        if t.completed_at is not None:
            tl.append({"at": aware(t.completed_at), "lane": "analyst", "kind": "task_completed",
                       "text": f"Task completed: {t.title}", "actor": emails.get(t.completed_by)})
    for r in enrich:
        if r.status == "ok" and r.verdict == "malicious" and r.fetched_at is not None:
            tl.append({"at": aware(r.fetched_at), "lane": "indicators", "kind": "enrichment",
                       "text": f"{r.source} flagged {r.indicator_type} as malicious", "actor": r.source})
    tl.sort(key=lambda e: e["at"])
    for n, e in enumerate(tl, 1):
        e["n"] = n

    # --- milestones ---
    overrides = {}
    if row:
        overrides = {k: v for k, v in (("first_seen", row.first_seen_at), ("detected", row.detected_at),
                                        ("contained", row.contained_at), ("recovered", row.recovered_at))
                     if v is not None}
    ms = milestones.compute(
        ioc_first_seen=[i.first_seen for i in iocs if i.first_seen is not None],
        alert_created=[a.created_at for a in alerts if a.created_at is not None],
        case_created=case.created_at,
        contained_candidates=[t.completed_at for t in done if t.phase == "containment" and t.completed_at],
        resolved_at=case.resolved_at, overrides=overrides)

    # --- actions ---
    actions: Dict[str, List[dict]] = {}
    for t in sorted(done, key=lambda t: (aware(t.completed_at) or datetime.max.replace(tzinfo=timezone.utc))):
        actions.setdefault(t.phase, []).append({"title": t.title, "completed_at": aware(t.completed_at),
                                                "completed_by": emails.get(t.completed_by)})
    actions = {p: actions[p] for p in sorted(actions, key=lambda p: (PHASES.index(p) if p in PHASES else len(PHASES), p))}
    outstanding = [{"title": t.title, "phase": t.phase} for t in tasks if t.status != "done"]

    evidence = [{"filename": a.filename, "size_bytes": a.size_bytes, "sha256": a.sha256,
                 "is_malicious": bool(a.is_malicious), "uploaded_by": emails.get(a.uploaded_by),
                 "created_at": aware(a.created_at)}
                for a in sorted((a for a in attachments if a.deleted_at is None),
                                key=lambda a: (aware(a.created_at) or datetime.min.replace(tzinfo=timezone.utc), a.id),
                                reverse=True)]

    sla = compute_sla_state(case, await load_policy_overrides(db, tid))
    report = {
        "meta": {"schema": SCHEMA, "generated_at": datetime.now(timezone.utc),
                 "generated_by": {"id": generated_by.id, "email": generated_by.email} if generated_by else None,
                 "app_version": APP_VERSION, "full": full},
        "case": {"id": cid, "title": case.title, "severity": _val(case.severity), "status": _val(case.status),
                 "owner": emails.get(case.owner_id), "created_at": aware(case.created_at),
                 "resolved_at": aware(case.resolved_at), "tags": list(case.tags or []),
                 "sla": {"response": sla["response_status"], "resolution": sla["resolution_status"]}},
        "report": {"executive_summary": row.executive_summary if row else None,
                   "impact": row.impact if row else None,
                   "lessons_learned": row.lessons_learned if row else None,
                   "tlp": row.tlp if row else "amber",
                   "updated_by": emails.get(row.updated_by) if row else None,
                   "updated_at": aware(row.updated_at) if row else None},
        "milestones": ms,
        "timeline": tl,
        "iocs": ioc_list,
        "actions": actions,
        "outstanding": outstanding,
        "evidence": evidence,
    }

    if full:
        att_ids = [a.id for a in attachments]
        cond = and_(AuditLog.entity_type == "case", AuditLog.entity_id == cid)
        if att_ids:
            cond = or_(cond, and_(AuditLog.entity_type == "attachment", AuditLog.entity_id.in_(att_ids)))
        logs = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == tid, cond)
                                 .order_by(AuditLog.created_at, AuditLog.id))).scalars().all()
        log_emails = await _emails(db, [l.user_id for l in logs])
        report["audit"] = [{"at": aware(l.created_at), "actor_email": log_emails.get(l.user_id),
                            "entity": f"{l.entity_type} #{l.entity_id}", "action": l.action} for l in logs]

    report["truncated"] = compute_truncated(report)
    return report


def to_jsonable(obj):
    """Recursively convert datetimes (and dates) to ISO 8601 strings with offset."""
    if isinstance(obj, datetime):
        return aware(obj).isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj
