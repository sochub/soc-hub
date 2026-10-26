"""Read model of an IOC's/artifact's threat-intel results, shared by the HTTP API (and the MCP server)."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enrichment.config import load_settings
from app.enrichment.indicators import normalise, tlp_allows
from app.enrichment.suggest import suggest
from app.models.artifact import Artifact
from app.models.enrichment_result import EnrichmentResult
from app.models.ioc import IOC

STALL = timedelta(minutes=10)


async def load_target(db: AsyncSession, kind: str, obj_id: int, tenant_id: int):
    """(obj, raw_type) for a tenant IOC or non-private artifact, or None when the kind or id is unknown."""
    model = IOC if kind == "ioc" else Artifact if kind == "artifact" else None
    if model is None:
        return None
    obj = (await db.execute(select(model).where(model.id == obj_id, model.tenant_id == tenant_id))).scalars().first()
    if obj is None or (kind == "artifact" and obj.is_private):  # private types are workflow-only: never sent out
        return None
    raw_type = obj.ioc_type if kind == "ioc" else getattr(obj.artifact_type, "value", obj.artifact_type)
    return obj, raw_type


def _iso(dt):
    return dt.isoformat() if dt else None


def result_dict(r: EnrichmentResult, now: datetime) -> dict:
    stalled = bool(r.status == "pending" and r.updated_at and now - r.updated_at > STALL)
    return {"source": r.source, "status": r.status, "verdict": r.verdict, "score": r.score,
            "summary": r.summary, "link": r.link, "error": r.error, "fetched_at": _iso(r.fetched_at),
            "stalled": stalled}


async def enrichment_view(db: AsyncSession, *, kind: str, obj_id: int, tenant_id: int) -> Optional[dict]:
    """Stored results, effective TLP and (for IOCs) a triage suggestion; None when not found in the tenant."""
    target = await load_target(db, kind, obj_id, tenant_id)
    if target is None:
        return None
    obj, raw_type = target
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
            "results": [result_dict(r, now) for r in rows], "suggestion": suggestion}
