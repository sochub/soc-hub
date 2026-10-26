from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.models.ioc import IOC
from app.models.user import User
from app.schemas import ioc as ioc_schema
from app.services import ioc_service

router = APIRouter()


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
    verdicts = await ioc_service.enrichment_verdicts(db, tenant_id, iocs)
    return [{**ioc_schema.IOC.model_validate(i).model_dump(), "enrichment_verdict": verdicts.get(i.id)}
            for i in iocs]


@router.post("/", response_model=ioc_schema.IOC)
async def create_ioc(
    *,
    db: AsyncSession = Depends(deps.get_db),
    ioc_in: ioc_schema.IOCCreate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create a new IOC."""
    ioc = await ioc_service.create_ioc(db, tenant_id=tenant_id, data=ioc_in.model_dump(), user_id=current_user.id)
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
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Update an IOC (tenant-scoped)."""
    bad = ioc_service.null_fields(ioc_in.model_dump(exclude_unset=True))
    if bad:
        raise HTTPException(status_code=422, detail=f"{', '.join(bad)} cannot be null")
    result = await db.execute(
        select(IOC).where(IOC.id == ioc_id, IOC.tenant_id == tenant_id)
    )
    ioc = result.scalars().first()
    if not ioc:
        raise HTTPException(status_code=404, detail="IOC not found")

    await ioc_service.update_ioc(db, ioc=ioc, data=ioc_in.model_dump(exclude_unset=True), tenant_id=tenant_id,
                                 user_id=current_user.id)
    await db.commit()
    await db.refresh(ioc)
    return ioc


@router.delete("/{ioc_id}")
async def delete_ioc(
    *,
    db: AsyncSession = Depends(deps.get_db),
    ioc_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Delete an IOC (tenant-scoped)."""
    result = await db.execute(
        select(IOC).where(IOC.id == ioc_id, IOC.tenant_id == tenant_id)
    )
    ioc = result.scalars().first()
    if not ioc:
        raise HTTPException(status_code=404, detail="IOC not found")

    await ioc_service.delete_ioc(db, ioc=ioc, tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    return {"ok": True}
