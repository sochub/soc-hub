from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from app.api import deps
from app.models.user import User
from app.tasks.jira import create_jira_ticket_task
from pydantic import BaseModel

router = APIRouter()

class JiraSyncRequest(BaseModel):
    case_id: int
    title: str
    description: str

@router.post("/jira/sync", status_code=202)
async def sync_jira_ticket(
    *,
    sync_in: JiraSyncRequest,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Trigger background task to create/sync Jira ticket (tenant-scoped)."""
    create_jira_ticket_task.delay(sync_in.case_id, sync_in.title, sync_in.description)
    return {"message": "Sync task started"}
