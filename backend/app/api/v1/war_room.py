from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.case import Case
from app.models.user import User
from app.models.war_room_suggestion import WarRoomSuggestion
from app.services import war_room_suggestions as svc

router = APIRouter()


class SuggestionOut(BaseModel):
    id: int
    kind: str
    payload: dict
    status: str
    permalink: Optional[str] = None
    created_at: Any = None

    class Config:
        from_attributes = True


async def _case_or_404(db: AsyncSession, case_id: int, tenant_id: int) -> Case:
    case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/{case_id}/war-room/suggestions", response_model=List[SuggestionOut])
async def list_suggestions(
    *, case_id: int, status: str = "pending",
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    await _case_or_404(db, case_id, tenant_id)
    res = await db.execute(select(WarRoomSuggestion).where(
        WarRoomSuggestion.case_id == case_id, WarRoomSuggestion.tenant_id == tenant_id,
        WarRoomSuggestion.status == status).order_by(WarRoomSuggestion.id))
    return res.scalars().all()


async def _resolve(db, case_id, sid, tenant_id, user, accept):
    await _case_or_404(db, case_id, tenant_id)
    owner = (await db.execute(select(WarRoomSuggestion.case_id).where(
        WarRoomSuggestion.id == sid, WarRoomSuggestion.tenant_id == tenant_id))).scalar()
    if owner != case_id:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    try:
        s = await svc.resolve_suggestion(db, tenant_id=tenant_id, suggestion_id=sid, accept=accept, user=user)
    except LookupError:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    await db.commit()
    return s


@router.post("/{case_id}/war-room/suggestions/{sid}/accept", response_model=SuggestionOut)
async def accept_suggestion(
    *, case_id: int, sid: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    return await _resolve(db, case_id, sid, tenant_id, current_user, True)


@router.post("/{case_id}/war-room/suggestions/{sid}/dismiss", response_model=SuggestionOut)
async def dismiss_suggestion(
    *, case_id: int, sid: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    return await _resolve(db, case_id, sid, tenant_id, current_user, False)
