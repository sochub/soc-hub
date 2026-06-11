from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel

from app.api import deps
from app.models.case import Case
from app.models.user import User
from app.tasks.jira import create_jira_ticket_task

router = APIRouter()

class JiraSyncRequest(BaseModel):
    case_id: int

@router.post("/jira/sync", status_code=202)
async def sync_jira_ticket(
    *,
    db: AsyncSession = Depends(deps.get_db),
    sync_in: JiraSyncRequest,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Trigger a background task to create/sync a Jira ticket for a case.

    The case is loaded and verified to belong to the caller's tenant; the ticket
    title/description are sourced from the case (not from caller input) to avoid
    cross-tenant references and content injection.
    """
    case = (await db.execute(
        select(Case).where(Case.id == sync_in.case_id, Case.tenant_id == tenant_id)
    )).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    create_jira_ticket_task.delay(case.id, case.title, case.description or "", tenant_id)
    return {"message": "Sync task started"}
