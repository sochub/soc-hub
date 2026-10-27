"""Public "was this you?" answer page for email_ask_user steps. No login: the 256-bit token in the link is the
credential, it only works while the step waits, and only for email questions. GET shows the question and records
nothing (mail scanners open links); POST records the answer once and resumes the run."""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.tenant import Tenant
from app.models.workflow import WorkflowRun, WorkflowRunStep
from app.utils.audit import create_audit_log

router = APIRouter()
CLOSED = {"state": "closed"}  # answered, expired, unknown and foreign tokens all look the same


class Answer(BaseModel):
    index: int = Field(ge=0, le=4)


async def _open_step(db: AsyncSession, token: str, *, lock: bool = False):
    if not 20 <= len(token) <= 64:
        return None
    q = (select(WorkflowRunStep, WorkflowRun.tenant_id, Tenant.name)
         .join(WorkflowRun, WorkflowRun.id == WorkflowRunStep.run_id).join(Tenant, Tenant.id == WorkflowRun.tenant_id)
         .where(WorkflowRunStep.wait_token == token, Tenant.is_active.isnot(False)))
    if lock:
        q = q.with_for_update(of=WorkflowRunStep)
    row = (await db.execute(q)).first()
    if row is None:
        return None
    step = row[0]
    out = step.output or {}
    if (step.status != "waiting" or out.get("channel_type") != "email"
            or (step.wait_expires_at and step.wait_expires_at <= datetime.now(timezone.utc))):
        return None
    return row


@router.get("/{token}")
async def get_question(token: str, db: AsyncSession = Depends(deps.get_db)) -> Any:
    row = await _open_step(db, token)
    if row is None:
        return CLOSED
    step, _, org = row
    return {"state": "open", "organization": org, "message": step.output["message"],
            "buttons": step.output["buttons"], "expires_at": step.wait_expires_at}


@router.post("/{token}")
async def answer(token: str, body: Answer, db: AsyncSession = Depends(deps.get_db)) -> Any:
    from app.tasks.workflows import advance_run_task

    row = await _open_step(db, token, lock=True)  # row lock: double submits resume the run at most once
    if row is None:
        raise HTTPException(status_code=410, detail="This question was already answered or has expired.")
    step, tenant_id, _ = row
    out = dict(step.output)
    if body.index >= len(out["buttons"]):
        raise HTTPException(status_code=422, detail="Unknown option")
    label = out["buttons"][body.index]
    step.output = {**out, "response": label, "responder_email": out["target_email"], "responder_slack_id": None,
                   "timed_out": False}
    step.status, step.finished_at = "succeeded", datetime.now(timezone.utc)
    run_id = step.run_id
    await create_audit_log(db=db, entity_type="workflow_run", entity_id=run_id, action="user_answered",
                           tenant_id=tenant_id, actor="end_user", changes={"response": label, "step": step.node_id})
    await db.commit()
    advance_run_task.delay(run_id)
    return {"state": "answered", "response": label}
