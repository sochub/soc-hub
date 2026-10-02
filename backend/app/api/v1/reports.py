"""Incident report: edit the narrative, draft it with AI, preview charts, export PDF/JSON."""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import llm
from app.ai.errors import AIError
from app.api import deps
from app.models.case import Case
from app.models.case_report import CaseReport
from app.models.user import User
from app.reports import milestones as ms_mod
from app.reports import tlp as tlp_mod
from app.reports.builder import build_report, min_tlp, report_row, to_jsonable
from app.reports.charts import lifecycle_svg, timeline_svg
from app.reports.pdf import RenderTimeout, render_pdf_async, strip_userinfo
from app.schemas.report import MILESTONE_FIELDS, TEXT_MAX, TLP, ReportDraft, ReportIn
from app.services.ai_service import _parse_json_reply
from app.utils.audit import create_audit_log

router = APIRouter()
logger = logging.getLogger(__name__)
# Never log narrative text, IOC values or AI replies: ids, formats and outcomes only.
_LOG = "report %s tenant=%s case=%s format=%s full=%s outcome=%s"
TEXT_FIELDS = ("executive_summary", "impact", "lessons_learned")
_FIELDS = (*TEXT_FIELDS, "tlp", *MILESTONE_FIELDS)
_DEFAULTS = {f: None for f in _FIELDS} | {"tlp": "amber"}
_SVG_HEADERS = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store"}
_DRAFT_SYSTEM = (
    "You write incident report narratives for a security operations team. Reply with ONLY a JSON object "
    'with exactly these string keys: {"executive_summary": "...", "impact": "...", "lessons_learned": "..."}. '
    "Write plain text, no markdown, for a management audience. Be factual: use only the case data provided, "
    "never invent systems, people, numbers or events, and say when something is unknown.")


def _log(op: str, tid, cid, outcome: str, fmt: str = "-", full: Any = "-") -> None:
    logger.info(_LOG, op, tid, cid, fmt, full, outcome)


async def _case(db: AsyncSession, op: str, case_id: int, tid: int, fmt: str = "-", full: Any = "-") -> Case:
    c = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tid))).scalars().first()
    if c is None:
        _log(op, tid, case_id, "not_found", fmt, full)
        raise HTTPException(status_code=404, detail="Case not found")
    return c


def _max_tlp(a: str, b: str) -> str:
    return a if tlp_mod.at_least(a, b) else b


def _tlp_floor_error(floor: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"TLP must be at least {tlp_mod.LABEL[floor]}")


async def _payload(db: AsyncSession, case: Case, user: User, tid: int) -> dict:
    row = await report_row(db, case)
    rep = await build_report(db, case, full=False, generated_by=user)
    report = {f: (getattr(row, f) if row else _DEFAULTS[f]) for f in _FIELDS}
    report.update(updated_by_email=rep["report"]["updated_by"], updated_at=rep["report"]["updated_at"])
    return to_jsonable({
        "report": report,
        "milestones": rep["milestones"],
        "min_tlp": await min_tlp(db, case),
        "ai_available": (await llm.describe(db, tid))["provider"] is not None,
    })


def _same(a, b) -> bool:
    if isinstance(a, datetime) or isinstance(b, datetime):
        return ms_mod.aware(a) == ms_mod.aware(b)
    return a == b


@router.get("/{case_id}/report")
async def get_report(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    case = await _case(db, "get", case_id, tenant_id)
    out = await _payload(db, case, current_user, tenant_id)
    _log("get", tenant_id, case_id, "ok")
    return out


@router.put("/{case_id}/report")
async def put_report(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    body: ReportIn,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    case = await _case(db, "put", case_id, tenant_id)
    floor = await min_tlp(db, case)
    if not tlp_mod.at_least(body.tlp, floor):
        _log("put", tenant_id, case_id, "invalid")
        raise _tlp_floor_error(floor)
    overrides = {k: getattr(body, f"{k}_at") for k in ms_mod.KEYS}
    if not ms_mod.ordered(overrides):
        _log("put", tenant_id, case_id, "invalid")
        raise HTTPException(status_code=422, detail="Milestones must be in chronological order")

    # Upsert: create the row if missing, then lock it so concurrent saves serialise and the diff is exact.
    created = (await db.execute(insert(CaseReport).values(tenant_id=tenant_id, case_id=case_id)
                                .on_conflict_do_nothing(index_elements=["case_id"])
                                .returning(CaseReport.id))).scalar_one_or_none()
    row = (await db.execute(select(CaseReport).where(CaseReport.case_id == case_id,
                                                     CaseReport.tenant_id == tenant_id)
                            .with_for_update().execution_options(populate_existing=True))).scalar_one()
    changed = []
    for f in _FIELDS:
        new = getattr(body, f)
        old = _DEFAULTS[f] if created is not None else getattr(row, f)
        if not _same(old, new):
            changed.append(f)
        setattr(row, f, new)
    row.updated_by = current_user.id
    row.updated_at = datetime.now(timezone.utc)
    await db.flush()
    # Field names only: the narrative never goes into the audit trail.
    await create_audit_log(db, entity_type="case_report", entity_id=row.id, action="update",
                           tenant_id=tenant_id, user_id=current_user.id, changes={"fields": changed})
    await db.commit()
    out = await _payload(db, case, current_user, tenant_id)
    _log("put", tenant_id, case_id, "ok")
    return out


def _draft_input(rep: dict) -> dict:
    iocs = [{**i, "value": strip_userinfo(str(i["value"])) if (i.get("type") or "").lower() == "url" else i["value"]}
            for i in rep["iocs"]]
    return to_jsonable({
        "case": rep["case"],
        "milestones": {k: rep["milestones"][k] for k in ms_mod.KEYS},
        "timeline": [{k: e[k] for k in ("n", "at", "lane", "kind", "text")} for e in rep["timeline"]],
        "iocs": iocs,
        "actions": rep["actions"],
        "outstanding": rep["outstanding"],
        "evidence": [{"filename": e["filename"], "sha256": e["sha256"]} for e in rep["evidence"]],
    })


@router.post("/{case_id}/report/draft", response_model=ReportDraft)
async def draft_report(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    case = await _case(db, "draft", case_id, tenant_id)
    if (await llm.describe(db, tenant_id))["provider"] is None:
        _log("draft", tenant_id, case_id, "no_ai")
        raise HTTPException(status_code=409, detail="AI is not configured")
    rep = await build_report(db, case, full=False, generated_by=current_user)
    await db.commit()  # release the connection before the (slow) provider call
    messages = [{"role": "system", "content": _DRAFT_SYSTEM},
                {"role": "user", "content": "Case data (JSON):\n" + json.dumps(_draft_input(rep), ensure_ascii=False)}]
    try:
        data = _parse_json_reply(await llm.complete(messages, tenant_id=tenant_id, json_mode=True))
    except AIError:
        data = None
    if data is None:
        _log("draft", tenant_id, case_id, "unavailable")
        raise HTTPException(status_code=503, detail="AI drafting is unavailable")
    _log("draft", tenant_id, case_id, "ok")
    return {f: ("" if data.get(f) is None else str(data.get(f)))[:TEXT_MAX] for f in TEXT_FIELDS}


@router.get("/{case_id}/report/charts/{name}.svg")
async def report_chart(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    name: str,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Response:
    case = await _case(db, "chart", case_id, tenant_id)
    if name not in ("lifecycle", "timeline"):
        _log("chart", tenant_id, case_id, "not_found")
        raise HTTPException(status_code=404, detail="Chart not found")
    rep = await build_report(db, case, full=False, generated_by=current_user)
    svg = lifecycle_svg(rep["milestones"]) if name == "lifecycle" else timeline_svg(rep["timeline"])
    _log("chart", tenant_id, case_id, "ok")
    return Response(svg, media_type="image/svg+xml", headers=dict(_SVG_HEADERS))


def _json_bytes(report: dict) -> bytes:
    out = to_jsonable(report)
    for i in out.get("iocs") or []:
        # R6: credentials never belong in a report; values otherwise stay raw (not defanged).
        if (i.get("type") or "").lower() == "url" and i.get("value") is not None:
            i["value"] = strip_userinfo(str(i["value"]))
    return json.dumps(out, ensure_ascii=False, indent=2).encode()


@router.get("/{case_id}/report/export")
async def export_report(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    format: Literal["pdf", "json"] = Query(...),
    full: bool = False,
    tlp: Optional[TLP] = None,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Response:
    case = await _case(db, "export", case_id, tenant_id, format, full)
    user_id = current_user.id
    floor = await min_tlp(db, case)
    if tlp is not None and not tlp_mod.at_least(tlp, floor):
        _log("export", tenant_id, case_id, "invalid", format, full)
        raise _tlp_floor_error(floor)
    report = await build_report(db, case, full=full, generated_by=current_user)
    chosen = _max_tlp(tlp or report["report"]["tlp"], floor)
    report["report"]["tlp"] = chosen
    await db.commit()  # release the connection while rendering
    try:
        if format == "pdf":
            body, media, ext = await render_pdf_async(report), "application/pdf", "pdf"
        else:
            body, media, ext = _json_bytes(report), "application/json", "json"
    except RenderTimeout:
        _log("export", tenant_id, case_id, "timeout", format, full)
        raise HTTPException(status_code=504, detail="Report rendering timed out")
    except Exception:
        _log("export", tenant_id, case_id, "error", format, full)
        raise
    sha = hashlib.sha256(body).hexdigest()
    await create_audit_log(db, entity_type="case", entity_id=case_id, action="export_report", tenant_id=tenant_id,
                           user_id=user_id, changes={"format": format, "full": full, "tlp": chosen, "sha256": sha,
                                                     "size_bytes": len(body)})
    await db.commit()
    day = ms_mod.aware(report["meta"]["generated_at"]).astimezone(timezone.utc).strftime("%Y%m%d")
    filename = f"case-{case_id:04d}-report-TLP-{tlp_mod.LABEL[chosen]}-{day}.{ext}"
    _log("export", tenant_id, case_id, "ok", format, full)
    return Response(body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "X-Content-SHA256": sha,
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
