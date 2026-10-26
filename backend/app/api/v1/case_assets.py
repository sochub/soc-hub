"""Minimal case assets: hosts/accounts/etc. involved in a case with a compromise status."""
from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Case
from app.models.case_asset import CaseAsset
from app.models.user import User
from app.schemas.case_asset import CaseAssetCreate, CaseAssetOut, CaseAssetUpdate
from app.services import case_records

router = APIRouter()


async def _get_case(db: AsyncSession, case_id: int, tenant_id: int) -> Case:
    res = await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))
    case = res.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def _get_asset(db: AsyncSession, case_id: int, asset_id: int, tenant_id: int) -> CaseAsset:
    res = await db.execute(select(CaseAsset).where(
        CaseAsset.id == asset_id, CaseAsset.case_id == case_id, CaseAsset.tenant_id == tenant_id))
    asset = res.scalars().first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.get("/{case_id}/assets", response_model=List[CaseAssetOut])
async def list_assets(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _get_case(db, case_id, tenant_id)
    res = await db.execute(select(CaseAsset).where(CaseAsset.case_id == case_id, CaseAsset.tenant_id == tenant_id)
                           .order_by(CaseAsset.name, CaseAsset.id))
    return res.scalars().all()


@router.post("/{case_id}/assets", response_model=CaseAssetOut, status_code=201)
async def add_asset(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    body: CaseAssetCreate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _get_case(db, case_id, tenant_id)
    asset = await case_records.add_case_asset(db, case_id=case_id, tenant_id=tenant_id, user_id=current_user.id,
                                              body=body)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.put("/{case_id}/assets/{asset_id}", response_model=CaseAssetOut)
async def update_asset(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    asset_id: int,
    body: CaseAssetUpdate,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    asset = await _get_asset(db, case_id, asset_id, tenant_id)
    await case_records.update_case_asset(db, asset=asset, data=body.model_dump(exclude_unset=True),
                                         tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete("/{case_id}/assets/{asset_id}", status_code=204)
async def delete_asset(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    asset_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    asset = await _get_asset(db, case_id, asset_id, tenant_id)
    await case_records.delete_case_asset(db, asset=asset, tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
