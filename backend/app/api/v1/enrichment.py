"""Threat-intel enrichment: tenant config (admin), per-indicator results (members) and manual runs (analyst+).

API keys are write-only: responses, audit rows and log lines only ever say which keys are set."""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import scrub
from app.api import deps
from app.core.config import settings
from app.enrichment import hooks
from app.enrichment.config import DEFAULTS, KEY_FIELDS, decrypt_keys, load_settings, runnable_sources
from app.enrichment.indicators import normalise, tlp_allows
from app.enrichment.sources import REGISTRY
from app.enrichment.suggest import suggest
from app.models.artifact import Artifact
from app.models.enrichment_result import EnrichmentResult
from app.models.ioc import IOC
from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.models.user import User
from app.schemas.enrichment import EnrichmentConfigIn, RunIn
from app.tasks.enrichment import upsert_result
from app.utils.audit import create_audit_log
from app.utils.crypto import encrypt

router = APIRouter()
logger = logging.getLogger(__name__)
_CONFIRM = "This sends the value to external threat-intel services (TLP:{tlp}). Confirm to continue."
_PLAIN = ("auto_max_tlp", "artifact_tlp", "cache_ttl_hours", "sources", "vt_per_minute", "vt_per_day",
          "internal_domains")
_CLEAR = {"virustotal_api_key": "clear_virustotal", "abusech_auth_key": "clear_abusech"}
_TESTS = (("virustotal", "virustotal", "virustotal_api_key"), ("abusech", "urlhaus", "abusech_auth_key"))
_STALL = timedelta(minutes=10)


def _log(op, tid, outcome):
    # Tenant id and outcome only — never keys, values or bodies.
    logger.info("ti_config %s tenant=%s outcome=%s", op, tid, outcome)


async def _row(db, tid) -> Optional[TenantEnrichmentConfig]:
    return (await db.execute(select(TenantEnrichmentConfig).where(
        TenantEnrichmentConfig.tenant_id == tid))).scalars().first()


def _out(row, keys) -> dict:
    base = {k: (getattr(row, k) if row is not None and getattr(row, k) is not None else DEFAULTS[k])
            for k in ("auto_max_tlp", "artifact_tlp", "cache_ttl_hours", "vt_per_minute", "vt_per_day")}
    base["sources"] = {**DEFAULTS["sources"], **((row.sources if row else None) or {})}
    base["internal_domains"] = list((row.internal_domains if row else None) or [])
    base["credentials_set"] = {"virustotal": bool(keys.get("virustotal_api_key")),
                               "abusech": bool(keys.get("abusech_auth_key"))}
    return base


async def _target(db, kind, obj_id, tid):
    model = IOC if kind == "ioc" else Artifact if kind == "artifact" else None
    if model is None:
        raise HTTPException(status_code=404, detail="Not found")
    obj = (await db.execute(select(model).where(model.id == obj_id, model.tenant_id == tid))).scalars().first()
    if obj is None:
        raise HTTPException(status_code=404, detail="Not found")
    raw_type = obj.ioc_type if kind == "ioc" else getattr(obj.artifact_type, "value", obj.artifact_type)
    return obj, raw_type


def _iso(dt):
    return dt.isoformat() if dt else None


def _result(r: EnrichmentResult, now: datetime) -> dict:
    stalled = bool(r.status == "pending" and r.updated_at and now - r.updated_at > _STALL)
    return {"source": r.source, "status": r.status, "verdict": r.verdict, "score": r.score,
            "summary": r.summary, "link": r.link, "error": r.error, "fetched_at": _iso(r.fetched_at),
            "stalled": stalled}


# ---------------------------------------------------------------- config (admin)

@router.get("/config")
async def get_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    row = await _row(db, tenant_id)
    keys = decrypt_keys(row.credentials_enc, tenant_id)[0] if row else {}
    return _out(row, keys)


@router.put("/config")
async def put_config(body: EnrichmentConfigIn, db: AsyncSession = Depends(deps.get_db),
                     current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    row = await _row(db, tenant_id)
    created = row is None
    stored, key_error = decrypt_keys(row.credentials_enc, tenant_id) if row else ({}, False)
    keys = dict(stored)
    touched = []
    for f in KEY_FIELDS:
        if getattr(body, _CLEAR[f]):
            if keys.pop(f, None) is not None or key_error:
                touched.append(f)
        v = (getattr(body, f) or "").strip()
        if v:
            keys[f] = v
            if f not in touched:
                touched.append(f)
    if created:
        row = TenantEnrichmentConfig(tenant_id=tenant_id)
        db.add(row)
    fields = []
    # A partial PUT merges: on an existing row only the fields actually sent change, so omitting a
    # field can never silently loosen what leaves the system. A new row takes the defaults.
    for f in (_PLAIN if created else [f for f in _PLAIN if f in body.model_fields_set]):
        v = getattr(body, f)
        if created or getattr(row, f) != v:
            fields.append(f)
        setattr(row, f, v)
    if touched or not key_error:  # an unreadable blob is kept until keys are re-entered or cleared
        row.credentials_enc = encrypt(json.dumps(keys)) if keys else None
    await db.flush()
    await create_audit_log(db, entity_type="enrichment_config", entity_id=row.id,
                           action="create" if created else "update", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"fields": fields, "credentials": touched})
    await db.commit()
    _log("save", tenant_id, "created" if created else "updated")
    return _out(row, keys)


@router.delete("/config", status_code=204)
async def delete_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    row = await _row(db, tenant_id)
    if row is None:
        _log("delete", tenant_id, "not_found")
        return Response(status_code=204)
    row_id = row.id
    await db.delete(row)
    await create_audit_log(db, entity_type="enrichment_config", entity_id=row_id, action="delete",
                           tenant_id=tenant_id, user_id=current_user.id, changes={"fields": [], "credentials": []})
    await db.commit()
    _log("delete", tenant_id, "deleted")
    return Response(status_code=204)


@router.post("/config/test")
async def test_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    s = await load_settings(db, tenant_id)
    secrets_ = list(s.keys.values())
    out = {}
    for label, source, field in _TESTS:
        key = s.keys.get(field)
        if not key:
            msg = "credentials could not be decrypted — re-enter them" if s.key_error else "No key set"
            out[label] = {"ok": False, "message": msg}
            continue
        try:
            res = await REGISTRY[source].lookup("ip", "8.8.8.8", key)
        except Exception as e:  # a source bug must surface as a failed test, not a 500
            out[label] = {"ok": False, "message": scrub(f"{source} failed ({type(e).__name__})", secrets_)}
            continue
        if res.status == "rate_limited":
            out[label] = {"ok": False, "message": "quota reached"}
        elif res.status == "error":
            out[label] = {"ok": False, "message": scrub(res.error or f"{source} error", secrets_)}
        else:
            out[label] = {"ok": True, "message": "ok"}
    oks = [v["ok"] for v in out.values()]
    _log("test", tenant_id, "ok" if all(oks) else "partial" if any(oks) else "failed")
    return out


# ---------------------------------------------------------------- results and runs

@router.get("/{kind}/{obj_id}")
async def get_results(kind: str, obj_id: int, db: AsyncSession = Depends(deps.get_db),
                      current_user: User = Depends(deps.get_current_active_user),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    obj, raw_type = await _target(db, kind, obj_id, tenant_id)
    s = await load_settings(db, tenant_id)
    tlp = obj.tlp if kind == "ioc" else s.artifact_tlp
    norm = normalise(raw_type, obj.value)
    rows = []
    if norm:
        rows = (await db.execute(select(EnrichmentResult).where(
            EnrichmentResult.tenant_id == tenant_id, EnrichmentResult.indicator_type == norm[0],
            EnrichmentResult.indicator_value == norm[1]).order_by(EnrichmentResult.source))).scalars().all()
    now = datetime.now(timezone.utc)
    suggestion = None
    if kind == "ioc" and rows:
        suggestion = suggest(obj.threat_level, obj.tags or [],
                             [{"source": r.source, "status": r.status, "verdict": r.verdict, "summary": r.summary}
                              for r in rows])
    return {"enrichable": norm is not None, "indicator_type": norm[0] if norm else None,
            "indicator_value": norm[1] if norm else None, "effective_tlp": tlp,
            "auto": bool(norm) and tlp_allows(tlp, s.auto_max_tlp),
            "results": [_result(r, now) for r in rows], "suggestion": suggestion}


@router.post("/{kind}/{obj_id}/run")
async def run(kind: str, obj_id: int, body: Optional[RunIn] = Body(None),
              db: AsyncSession = Depends(deps.get_db),
              current_user: User = Depends(deps.require_analyst_or_above),
              tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    obj, raw_type = await _target(db, kind, obj_id, tenant_id)
    if not settings.ENRICHMENT_ENABLED:  # kill switch: nothing is queued and no pending rows are written
        raise HTTPException(status_code=503, detail="Threat-intel enrichment is disabled")
    value = obj.value
    s = await load_settings(db, tenant_id)
    tlp = obj.tlp if kind == "ioc" else s.artifact_tlp
    norm = normalise(raw_type, value)
    if norm is None:
        raise HTTPException(status_code=422, detail="This value can't be enriched")
    # Anything but white/green (incl. a missing or unrecognised label) needs explicit confirmation.
    if tlp not in ("white", "green") and not (body and body.confirm):
        raise HTTPException(status_code=409, detail=_CONFIRM.format(tlp=tlp or "unknown"))
    itype, v = norm
    now = datetime.now(timezone.utc)
    rows = []
    for name in runnable_sources(s):
        if name in REGISTRY and itype in REGISTRY[name].TYPES:
            rows.append(await upsert_result(db, tenant_id, itype, v, name, status="pending", error=None))
    hooks.enqueue(tenant_id, raw_type, value, tlp, force=True)
    return Response(status_code=202, media_type="application/json",
                    content=json.dumps({"results": [_result(r, now) for r in rows]}))
