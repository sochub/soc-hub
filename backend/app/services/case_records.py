"""Investigation timeline events and case assets, shared by the HTTP API and the MCP server.

All functions only flush; the caller commits. Callers load the event/asset tenant-scoped first.
"""
from typing import List

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact
from app.models.case_artifact import CaseArtifact
from app.models.case_asset import CaseAsset
from app.models.case_event import CaseEvent
from app.schemas.case_asset import CaseAssetCreate
from app.schemas.case_event import CaseEventCreate
from app.utils.audit import create_audit_log


async def event_artifacts(db: AsyncSession, case_id: int, tenant_id: int, ids: List[int]) -> List[Artifact]:
    """Only artifacts already attached to this case (and tenant) may be linked; anything else is a 400."""
    if not ids:
        return []
    res = await db.execute(
        select(Artifact).join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(CaseArtifact.case_id == case_id, Artifact.tenant_id == tenant_id, Artifact.id.in_(set(ids))))
    found = res.scalars().unique().all()
    if len(found) != len(set(ids)):
        raise HTTPException(status_code=400, detail="artifact_ids must belong to this case")
    return list(found)


async def add_case_event(db: AsyncSession, *, case_id: int, tenant_id: int, user_id: int,
                         body: CaseEventCreate) -> CaseEvent:
    ev = CaseEvent(case_id=case_id, tenant_id=tenant_id, user_id=user_id, **body.model_dump(exclude={"artifact_ids"}))
    ev.artifacts = await event_artifacts(db, case_id, tenant_id, body.artifact_ids)
    db.add(ev)
    await db.flush()
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="event_added",
                           tenant_id=tenant_id, user_id=user_id,
                           changes={"title": ev.title, "event_date": ev.event_date.isoformat()})
    return ev


async def update_case_event(db: AsyncSession, *, ev: CaseEvent, data: dict, tenant_id: int, user_id: int) -> CaseEvent:
    """``data`` is ``CaseEventUpdate.model_dump(exclude_unset=True)``."""
    data = dict(data)
    ids = data.pop("artifact_ids", None)
    for k, v in data.items():
        setattr(ev, k, v)
    if ids is not None:
        ev.artifacts = await event_artifacts(db, ev.case_id, tenant_id, ids)
    await create_audit_log(db=db, entity_type="case", entity_id=ev.case_id, action="event_updated",
                           tenant_id=tenant_id, user_id=user_id,
                           changes={"id": ev.id, "fields": sorted(data) + (["artifact_ids"] if ids is not None else [])})
    await db.flush()
    return ev


async def delete_case_event(db: AsyncSession, *, ev: CaseEvent, tenant_id: int, user_id: int) -> None:
    case_id, event_id, title = ev.case_id, ev.id, ev.title
    await db.delete(ev)
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="event_deleted",
                           tenant_id=tenant_id, user_id=user_id, changes={"id": event_id, "title": title})
    await db.flush()


async def add_case_asset(db: AsyncSession, *, case_id: int, tenant_id: int, user_id: int,
                         body: CaseAssetCreate) -> CaseAsset:
    asset = CaseAsset(case_id=case_id, tenant_id=tenant_id, created_by=user_id, **body.model_dump())
    db.add(asset)
    await db.flush()
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="asset_added",
                           tenant_id=tenant_id, user_id=user_id,
                           changes={"name": asset.name, "asset_type": asset.asset_type})
    return asset


async def update_case_asset(db: AsyncSession, *, asset: CaseAsset, data: dict, tenant_id: int,
                            user_id: int) -> CaseAsset:
    """``data`` is ``CaseAssetUpdate.model_dump(exclude_unset=True)``."""
    for k, v in data.items():
        setattr(asset, k, v)
    await create_audit_log(db=db, entity_type="case", entity_id=asset.case_id, action="asset_updated",
                           tenant_id=tenant_id, user_id=user_id, changes={"id": asset.id, "fields": sorted(data)})
    await db.flush()
    return asset


async def delete_case_asset(db: AsyncSession, *, asset: CaseAsset, tenant_id: int, user_id: int) -> None:
    case_id, asset_id, name = asset.case_id, asset.id, asset.name
    await db.delete(asset)
    await create_audit_log(db=db, entity_type="case", entity_id=case_id, action="asset_deleted",
                           tenant_id=tenant_id, user_id=user_id, changes={"id": asset_id, "name": name})
    await db.flush()
