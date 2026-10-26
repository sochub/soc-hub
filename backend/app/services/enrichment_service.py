"""Manual threat-intel enrichment runs, shared by the HTTP API and the MCP server."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.enrichment import hooks
from app.enrichment.config import load_settings, runnable_sources
from app.enrichment.indicators import normalise
from app.enrichment.sources import REGISTRY
from app.enrichment.view import load_target, result_dict
from app.tasks.enrichment import upsert_result

CONFIRM = "This sends the value to external threat-intel services (TLP:{tlp}). Confirm to continue."


async def request_run(db: AsyncSession, *, kind: str, obj_id: int, tenant_id: int, confirm: bool) -> list:
    """Mark each runnable source pending (commits per row) and queue a forced lookup. Returns the pending result
    dicts. 404 unknown target, 503 kill switch, 422 not enrichable, 409 TLP above green without ``confirm``."""
    target = await load_target(db, kind, obj_id, tenant_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Not found")
    obj, raw_type = target
    if not settings.ENRICHMENT_ENABLED:  # kill switch: nothing is queued and no pending rows are written
        raise HTTPException(status_code=503, detail="Threat-intel enrichment is disabled")
    value = obj.value
    s = await load_settings(db, tenant_id)
    tlp = obj.tlp if kind == "ioc" else s.artifact_tlp
    norm = normalise(raw_type, value)
    if norm is None:
        raise HTTPException(status_code=422, detail="This value can't be enriched")
    # Anything but white/green (incl. a missing or unrecognised label) needs explicit confirmation.
    if tlp not in ("white", "green") and not confirm:
        raise HTTPException(status_code=409, detail=CONFIRM.format(tlp=tlp or "unknown"))
    itype, v = norm
    now = datetime.now(timezone.utc)
    rows = []
    for name in runnable_sources(s):
        if name in REGISTRY and itype in REGISTRY[name].TYPES:
            rows.append(await upsert_result(db, tenant_id, itype, v, name, status="pending", error=None))
    hooks.enqueue(tenant_id, raw_type, value, tlp, force=True)
    return [result_dict(r, now) for r in rows]
