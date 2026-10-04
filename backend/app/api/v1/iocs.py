from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.enrichment.indicators import normalise
from app.models.enrichment_result import EnrichmentResult
from app.models.ioc import IOC
from app.models.user import User
from app.schemas import ioc as ioc_schema
from app.utils.audit import create_audit_log

router = APIRouter()
_NOT_NULL = {"ioc_type", "value", "threat_level", "confidence", "status", "tlp", "tags"}


@router.get("/", response_model=List[ioc_schema.IOC])
async def read_iocs(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
    case_id: Optional[int] = None,
    status: Optional[str] = None,
    ioc_type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> Any:
    """List all IOCs scoped to tenant."""
    query = select(IOC).where(IOC.tenant_id == tenant_id)
    if case_id is not None:
        query = query.where(IOC.case_id == case_id)
    if status is not None:
        query = query.where(IOC.status == status)
    if ioc_type is not None:
        query = query.where(IOC.ioc_type == ioc_type)
    query = query.offset(skip).limit(limit).order_by(IOC.created_at.desc())
    result = await db.execute(query)
    iocs = result.scalars().all()
    verdicts = await _verdicts(db, tenant_id, iocs)
    return [{**ioc_schema.IOC.model_validate(i).model_dump(), "enrichment_verdict": verdicts.get(i.id)}
            for i in iocs]


_RANK = {"malicious": 3, "suspicious": 2, "harmless": 1, "unknown": 0}


async def _verdicts(db: AsyncSession, tenant_id: int, iocs) -> dict:
    """Worst ok enrichment verdict per IOC id, from one query over the page's normalised indicators."""
    pairs = {}
    for ioc in iocs:
        n = normalise(ioc.ioc_type, ioc.value)
        if n:
            pairs[ioc.id] = n
    if not pairs:
        return {}
    rows = (await db.execute(select(
        EnrichmentResult.indicator_type, EnrichmentResult.indicator_value, EnrichmentResult.verdict).where(
        EnrichmentResult.tenant_id == tenant_id, EnrichmentResult.status == "ok",
        EnrichmentResult.indicator_type.in_({t for t, _ in pairs.values()}),
        EnrichmentResult.indicator_value.in_({v for _, v in pairs.values()})))).all()
    worst = {}
    for t, v, verdict in rows:
        if verdict in _RANK and _RANK[verdict] > _RANK.get(worst.get((t, v)), -1):
            worst[(t, v)] = verdict
    return {ioc_id: worst.get(pair) for ioc_id, pair in pairs.items()}


@router.post("/", response_model=ioc_schema.IOC)
async def create_ioc(
    *,
    db: AsyncSession = Depends(deps.get_db),
    ioc_in: ioc_schema.IOCCreate,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create a new IOC."""
    ioc = IOC(
        **ioc_in.model_dump(),
        tenant_id=tenant_id,
        created_by=current_user.id,
    )
    db.add(ioc)
    await db.flush()
    await create_audit_log(
        db=db,
        entity_type="ioc",
        entity_id=ioc.id,
        action="create",
        tenant_id=tenant_id,
        user_id=current_user.id,
    )
    await db.commit()
    await db.refresh(ioc)
    return ioc


@router.get("/{ioc_id}", response_model=ioc_schema.IOC)
async def read_ioc(
    *,
    db: AsyncSession = Depends(deps.get_db),
    ioc_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Get a single IOC (tenant-scoped)."""
    result = await db.execute(
        select(IOC).where(IOC.id == ioc_id, IOC.tenant_id == tenant_id)
    )
    ioc = result.scalars().first()
    if not ioc:
        raise HTTPException(status_code=404, detail="IOC not found")
    return ioc


@router.put("/{ioc_id}", response_model=ioc_schema.IOC)
async def update_ioc(
    *,
    db: AsyncSession = Depends(deps.get_db),
    ioc_id: int,
    ioc_in: ioc_schema.IOCUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Update an IOC (tenant-scoped)."""
    # Explicit nulls on non-nullable fields would break IOC list serialisation for the whole tenant.
    bad = sorted(k for k, v in ioc_in.model_dump(exclude_unset=True).items() if v is None and k in _NOT_NULL)
    if bad:
        raise HTTPException(status_code=422, detail=f"{', '.join(bad)} cannot be null")
    result = await db.execute(
        select(IOC).where(IOC.id == ioc_id, IOC.tenant_id == tenant_id)
    )
    ioc = result.scalars().first()
    if not ioc:
        raise HTTPException(status_code=404, detail="IOC not found")

    update_data = ioc_in.model_dump(exclude_unset=True)
    changes = {}
    for field, value in update_data.items():
        old_value = getattr(ioc, field)
        if old_value != value:
            changes[field] = {"from": str(old_value) if old_value is not None else None, "to": str(value) if value is not None else None}
        setattr(ioc, field, value)

    if changes:
        await create_audit_log(
            db=db,
            entity_type="ioc",
            entity_id=ioc.id,
            action="update",
            tenant_id=tenant_id,
            user_id=current_user.id,
            changes=changes,
        )

    await db.commit()
    await db.refresh(ioc)
    return ioc


@router.delete("/{ioc_id}")
async def delete_ioc(
    *,
    db: AsyncSession = Depends(deps.get_db),
    ioc_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Delete an IOC (tenant-scoped)."""
    result = await db.execute(
        select(IOC).where(IOC.id == ioc_id, IOC.tenant_id == tenant_id)
    )
    ioc = result.scalars().first()
    if not ioc:
        raise HTTPException(status_code=404, detail="IOC not found")

    await create_audit_log(
        db=db,
        entity_type="ioc",
        entity_id=ioc.id,
        action="delete",
        tenant_id=tenant_id,
        user_id=current_user.id,
    )
    await db.delete(ioc)
    await db.commit()
    return {"ok": True}
