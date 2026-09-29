from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.slack_integration import SlackIntegration
from app.models.user import User
from app.services.slack_service import SlackError, slack_call
from app.utils.audit import create_audit_log
from app.utils.crypto import decrypt, encrypt

router = APIRouter()


class SlackConfigIn(BaseModel):
    bot_token: Optional[str] = None
    signing_secret: Optional[str] = None
    default_channel: Optional[str] = None


async def _get(db, tenant_id: int) -> Optional[SlackIntegration]:
    return (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == tenant_id))).scalars().first()


def _public(integ: Optional[SlackIntegration]) -> dict:
    if not integ:
        return {"configured": False, "team_id": None, "default_channel": None, "has_bot_token": False, "has_signing_secret": False}
    return {"configured": True, "team_id": integ.team_id, "default_channel": integ.default_channel,
            "has_bot_token": True, "has_signing_secret": True}


@router.get("/config")
async def get_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    return _public(await _get(db, tenant_id))


@router.put("/config")
async def put_config(body: SlackConfigIn, db: AsyncSession = Depends(deps.get_db),
                     current_user: User = Depends(deps.require_admin),
                     tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    integ = await _get(db, tenant_id)
    token, secret = (body.bot_token or "").strip(), (body.signing_secret or "").strip()
    if not integ:
        if not token or not secret:
            raise HTTPException(status_code=400, detail="Bot token and signing secret are required")
        integ = SlackIntegration(tenant_id=tenant_id, bot_token_enc=encrypt(token), signing_secret_enc=encrypt(secret))
        db.add(integ)
    else:
        if token:
            integ.bot_token_enc, integ.team_id = encrypt(token), None  # re-run Test to learn the team
        if secret:
            integ.signing_secret_enc = encrypt(secret)
    if body.default_channel is not None:
        integ.default_channel = body.default_channel.strip() or None
    await db.flush()
    await create_audit_log(db=db, entity_type="slack_integration", entity_id=integ.id, action="update",
                           tenant_id=tenant_id, user_id=current_user.id,
                           changes={"token_changed": bool(token), "secret_changed": bool(secret)})
    await db.commit()
    return _public(integ)


@router.post("/config/test")
async def test_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    integ = await _get(db, tenant_id)
    if not integ:
        raise HTTPException(status_code=400, detail="Save a bot token and signing secret first")
    token = decrypt(integ.bot_token_enc)
    try:
        auth = await slack_call(token, "auth.test")
        integ.team_id = auth["team_id"]
        await db.commit()
        if integ.default_channel:
            await slack_call(token, "chat.postMessage", channel=integ.default_channel,
                             text=":white_check_mark: SOC Hub is connected to this channel.")
    except SlackError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "team": auth.get("team"), "team_id": integ.team_id}


@router.delete("/config", status_code=204)
async def delete_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Response:
    integ = await _get(db, tenant_id)
    if integ:
        await create_audit_log(db=db, entity_type="slack_integration", entity_id=integ.id, action="delete",
                               tenant_id=tenant_id, user_id=current_user.id)
        await db.delete(integ)
        await db.commit()
    return Response(status_code=204)
