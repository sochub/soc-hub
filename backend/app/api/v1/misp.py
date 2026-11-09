"""Push case IOCs to the tenant's MISP server."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.enrichment.config import load_settings, runnable_sources
from app.enrichment.sources.base import SourceError
from app.enrichment.sources.misp import parse_cfg
from app.models.case import Case
from app.models.ioc import IOC
from app.models.user import User
from app.services.misp_push import MispPushError, push_iocs
from app.utils.audit import create_audit_log

router = APIRouter()


class MispPushIn(BaseModel):
    ioc_ids: List[int] = Field(min_length=1, max_length=500)


@router.post("/{case_id}/misp-push")
async def misp_push(case_id: int, body: MispPushIn, db: AsyncSession = Depends(deps.get_db),
                    tenant_id: int = Depends(deps.get_effective_tenant_id),
                    current_user: User = Depends(deps.require_analyst_or_above)):
    case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    ids = list(dict.fromkeys(body.ioc_ids))
    iocs = (await db.execute(select(IOC).where(IOC.id.in_(ids), IOC.tenant_id == tenant_id,
                                               IOC.case_id == case_id).order_by(IOC.id))).scalars().all()
    if len(iocs) != len(ids):
        raise HTTPException(status_code=400, detail="All IOCs must belong to this case")
    s = await load_settings(db, tenant_id)
    if "misp" not in runnable_sources(s):
        raise HTTPException(status_code=409, detail="misp_not_configured")
    try:
        cfg = parse_cfg(s.keys["misp"])
    except SourceError:
        raise HTTPException(status_code=409, detail="misp_not_configured") from None
    try:
        res = await push_iocs(db, tenant_id=tenant_id, case=case, iocs=iocs, misp_cfg=cfg)
    except MispPushError as e:
        # Keep an event link created before the failure (a retry reuses it) and record what already left the tenant.
        await create_audit_log(db, "case", case_id, "misp_push", tenant_id, user_id=current_user.id,
                               changes={"partial": True, "pushed": len(e.pushed), "skipped": len(e.skipped)})
        await db.commit()
        raise HTTPException(status_code=502, detail={"message": str(e), "pushed_ids": e.pushed}) from None
    await create_audit_log(db, "case", case_id, "misp_push", tenant_id, user_id=current_user.id,
                           changes={"pushed": len(res["pushed"]), "skipped": len(res["skipped"]),
                                    "event_uuid": res["event_uuid"]})
    await db.commit()
    return res
