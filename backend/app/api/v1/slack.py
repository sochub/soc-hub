import json
import logging
from typing import Any, Literal, Optional
from urllib.parse import parse_qs

from cryptography.fernet import InvalidToken

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.models.slack_integration import SlackIntegration
from app.models.user import User
from app.services.slack_service import SlackError, slack_call, verify_signature
from app.utils.audit import create_audit_log
from app.utils.crypto import decrypt, encrypt

router = APIRouter()
logger = logging.getLogger(__name__)


class SlackConfigIn(BaseModel):
    bot_token: Optional[str] = None
    signing_secret: Optional[str] = None
    default_channel: Optional[str] = None
    alert_channel: Optional[str] = None
    # "" turns responder alerts off
    alert_min_severity: Optional[Literal["", "critical", "high", "medium", "low", "info"]] = None


async def _get(db, tenant_id: int) -> Optional[SlackIntegration]:
    return (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == tenant_id))).scalars().first()


def _public(integ: Optional[SlackIntegration]) -> dict:
    if not integ:
        return {"configured": False, "team_id": None, "default_channel": None, "has_bot_token": False,
                "has_signing_secret": False, "alert_channel": None, "alert_min_severity": None}
    return {"configured": True, "team_id": integ.team_id, "default_channel": integ.default_channel,
            "has_bot_token": True, "has_signing_secret": True, "alert_channel": integ.alert_channel,
            "alert_min_severity": integ.alert_min_severity}


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
    if body.alert_channel is not None:
        integ.alert_channel = body.alert_channel.strip() or None
    if body.alert_min_severity is not None:
        integ.alert_min_severity = body.alert_min_severity or None
    await db.flush()
    await create_audit_log(db=db, entity_type="slack_integration", entity_id=integ.id, action="update",
                           tenant_id=tenant_id, user_id=current_user.id,
                           changes={"token_changed": bool(token), "secret_changed": bool(secret),
                                    "alert_channel": integ.alert_channel, "alert_min_severity": integ.alert_min_severity})
    await db.commit()
    return _public(integ)


@router.post("/config/test")
async def test_config(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                      tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    integ = await _get(db, tenant_id)
    if not integ:
        raise HTTPException(status_code=400, detail="Save a bot token and signing secret first")
    try:
        token = decrypt(integ.bot_token_enc)
    except InvalidToken:
        raise HTTPException(status_code=400, detail="Slack credentials can't be decrypted (SECRET_KEY changed?) — re-enter them in Integrations")
    try:
        auth = await slack_call(token, "auth.test")
        integ.team_id = auth["team_id"]
        await db.commit()
        if integ.default_channel:
            await slack_call(token, "chat.postMessage", channel=integ.default_channel,
                             text=":white_check_mark: sochub is connected to this channel.")
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


def _secret_matches(integ: SlackIntegration, ts: str, body: bytes, sig: str) -> bool:
    try:
        secret = decrypt(integ.signing_secret_enc)
    except InvalidToken:
        return False  # undecryptable secret can never verify; don't 500 the public endpoint
    return verify_signature(secret, ts, body, sig)


_MAX_BODY = 65536


async def _verified(request: Request, db: AsyncSession, body: bytes, team_id) -> SlackIntegration:
    """The integration whose signing secret signed this request, or 401. Nothing runs before this passes."""
    if not isinstance(team_id, str) or not team_id:
        raise HTTPException(status_code=400, detail="bad payload")
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    candidates = (await db.execute(select(SlackIntegration).where(SlackIntegration.team_id == team_id))).scalars().all()
    match = next((i for i in candidates if _secret_matches(i, ts, body, sig)), None)
    if not match:
        logger.warning("rejected Slack request for team %s (bad signature or unknown team)", team_id)
        raise HTTPException(status_code=401, detail="invalid signature")
    return match


async def _body(request: Request) -> bytes:
    body = await request.body()
    if len(body) > _MAX_BODY:
        raise HTTPException(status_code=413, detail="payload too large")
    return body


@router.post("/interactions")
async def interactions(request: Request, db: AsyncSession = Depends(deps.get_db)) -> Response:
    """Slack interactivity callback. Unauthenticated by design: trust comes ONLY from the
    signature check; nothing is turned into actions before it passes."""
    from app.services import slack_bot
    from app.tasks.workflows import handle_slack_interaction_task

    body = await _body(request)
    try:
        payload = json.loads(parse_qs(body.decode())["payload"][0])
        team_id = payload["team"]["id"]
    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="bad payload")
    match = await _verified(request, db, body, team_id)

    kind = payload.get("type")
    try:
        if kind == "view_submission":
            out = await slack_bot.handle_submission(db, match, payload)
            return JSONResponse(out) if out else Response(status_code=200)
        if kind in ("shortcut", "message_action", "block_actions") and await slack_bot.open_modal_for(db, match, payload):
            return Response(status_code=200)
    except SlackError:
        logger.exception("Slack interaction failed for tenant %s", match.tenant_id)
        return Response(status_code=200)
    if kind == "block_actions":
        handle_slack_interaction_task.delay(match.tenant_id, payload)
    return Response(status_code=200)  # Slack needs an ack within 3 s; slower work happens in Celery


@router.post("/commands")
async def commands(request: Request, db: AsyncSession = Depends(deps.get_db)) -> Response:
    """`/sochub` slash command. Signature-verified like /interactions; replies are ephemeral."""
    from app.services import slack_bot

    body = await _body(request)
    form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
    match = await _verified(request, db, body, form.get("team_id"))
    try:
        out = await slack_bot.handle_command(db, match, form)
    except SlackError:
        logger.exception("Slack command failed for tenant %s", match.tenant_id)
        out = {"response_type": "ephemeral", "text": "Something went wrong talking to Slack. Try again."}
    return JSONResponse(out) if out else Response(status_code=200)


@router.post("/events")
async def events(request: Request, db: AsyncSession = Depends(deps.get_db)) -> Response:
    """Events API (App Home). URL verification is answered only for a request signed by a configured app."""
    from app.tasks.slack import slack_home_task

    body = await _body(request)
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad payload")
    if payload.get("type") == "url_verification":
        ts = request.headers.get("X-Slack-Request-Timestamp", "")
        sig = request.headers.get("X-Slack-Signature", "")
        integrations = (await db.execute(select(SlackIntegration))).scalars().all()
        if not any(_secret_matches(i, ts, body, sig) for i in integrations):
            raise HTTPException(status_code=401, detail="invalid signature")
        return JSONResponse({"challenge": str(payload.get("challenge", ""))[:200]})
    match = await _verified(request, db, body, payload.get("team_id"))
    event = payload.get("event") or {}
    if event.get("type") == "app_home_opened" and event.get("tab") == "home" and event.get("user"):
        slack_home_task.delay(match.tenant_id, event["user"])
    return Response(status_code=200)
