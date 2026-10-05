from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.artifact import Artifact
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.user import User
from app.schemas.artifact_type import ArtifactTypeCreate, ArtifactTypeOut, ArtifactTypeUpdate
from app.services.artifact_types import BUILTIN_KEYS

router = APIRouter()


async def _get(db, tenant_id: int, type_id: int) -> ArtifactTypeDefinition:
    d = (await db.execute(select(ArtifactTypeDefinition).where(
        ArtifactTypeDefinition.id == type_id, ArtifactTypeDefinition.tenant_id == tenant_id))).scalars().first()
    if not d:
        raise HTTPException(status_code=404, detail="Artifact type not found")
    return d


@router.get("/", response_model=List[ArtifactTypeOut])
async def list_types(db: AsyncSession = Depends(deps.get_db),
                     current_user: User = Depends(deps.get_current_active_user),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return (await db.execute(select(ArtifactTypeDefinition).where(ArtifactTypeDefinition.tenant_id == tenant_id)
                             .order_by(ArtifactTypeDefinition.id))).scalars().all()


@router.post("/", response_model=ArtifactTypeOut, status_code=201)
async def create_type(body: ArtifactTypeCreate, db: AsyncSession = Depends(deps.get_db),
                      current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    if body.key in BUILTIN_KEYS:
        raise HTTPException(status_code=400, detail=f"'{body.key}' is a built-in type")
    d = ArtifactTypeDefinition(tenant_id=tenant_id, **body.model_dump())
    db.add(d)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Type '{body.key}' already exists")
    await db.refresh(d)
    return d


@router.put("/{type_id}", response_model=ArtifactTypeOut)
async def update_type(type_id: int, body: ArtifactTypeUpdate, db: AsyncSession = Depends(deps.get_db),
                      current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    d = await _get(db, tenant_id, type_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    await db.commit()
    await db.refresh(d)
    return d


@router.delete("/{type_id}", status_code=204)
async def delete_type(type_id: int, db: AsyncSession = Depends(deps.get_db),
                      current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    d = await _get(db, tenant_id, type_id)
    used = (await db.execute(select(func.count(Artifact.id)).where(Artifact.custom_type_id == d.id))).scalar()
    if used:
        raise HTTPException(status_code=409, detail=f"{used} artifact(s) still use this type; delete them first")
    await db.delete(d)
    await db.commit()
    return Response(status_code=204)
