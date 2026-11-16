"""Public ack webhooks from PagerDuty / Opsgenie. Unauthenticated by design: this is a trust boundary.

Order per request: integration by opaque URL key (unknown key, deactivated tenant or vendor credential not
configured -> the same 404) -> per-key rate limit (fails open) -> body read with a 64 KiB cap -> signature/token
checked in constant time over the raw bytes -> JSON parse -> enqueue. All work happens in `process_ack_task`;
these handlers only validate and answer 202. Neither secrets nor request bodies are logged.
"""
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Optional

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.login_throttle import get_login_throttle
from app.escalations.service import parse_dedup_key
from app.models.escalation import EscalationIntegration
from app.models.tenant import Tenant
from app.utils.crypto import decrypt

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_BODY = 64 * 1024
PER_MINUTE = 120
MAX_SIGNATURES = 8
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{20,64}")
_PD_ID_RE = re.compile(r"[A-Za-z0-9]{1,32}")  # interpolated into PagerDuty REST paths by the task


def get_inbound_redis():
    """Same lazy Redis client as the login throttle; tests override it."""
    return get_login_throttle().redis


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


def _secret(enc: Optional[str]) -> Optional[str]:
    if not enc:
        return None
    try:
        return decrypt(enc) or None
    except InvalidToken:  # undecryptable counts as not configured; never 500 the public endpoint
        return None


async def _integration(db: AsyncSession, inbound_key: str) -> EscalationIntegration:
    if not _KEY_RE.fullmatch(inbound_key):
        raise _not_found()
    integ = (await db.execute(
        select(EscalationIntegration).join(Tenant, Tenant.id == EscalationIntegration.tenant_id)
        .where(EscalationIntegration.inbound_key == inbound_key, Tenant.is_active.is_(True)))).scalars().first()
    if integ is None:
        raise _not_found()
    return integ


async def _rate_limit(redis, integration_id: int) -> None:
    """Fixed one-minute window per inbound key (copied from the alert webhook limiter). Fails open."""
    key = f"escrl:{integration_id}:{int(time.time() // 60)}"
    try:
        n = await redis.incr(key)
        await redis.expire(key, 60, nx=True)
    except Exception as exc:
        logger.warning("escalation inbound limiter unavailable: %s", type(exc).__name__)
        return
    if n > PER_MINUTE:
        raise HTTPException(status_code=429, detail="Too many requests; slow down", headers={"Retry-After": "60"})


async def _body(request: Request) -> bytes:
    """The raw body, refusing more than MAX_BODY bytes without buffering them."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY:
                raise HTTPException(status_code=413, detail="payload too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="bad content-length") from None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise HTTPException(status_code=413, detail="payload too large")
    return bytes(buf)


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        raise HTTPException(status_code=400, detail="bad payload") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="bad payload")
    return data


def _obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value, limit: int) -> Optional[str]:
    return (value.strip()[:limit] or None) if isinstance(value, str) else None


def _pd_signature_ok(secret: str, raw: bytes, header: str) -> bool:
    expected = "v1=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    ok = False
    for candidate in header.split(",")[:MAX_SIGNATURES]:
        ok |= hmac.compare_digest(candidate.strip().encode(), expected.encode())  # no early exit
    return ok


def _enqueue(*args, **kwargs) -> None:
    from app.tasks.escalations import process_ack_task
    process_ack_task.delay(*args, **kwargs)


@router.post("/{inbound_key}/pagerduty", status_code=202)
async def pagerduty(inbound_key: str, request: Request, db: AsyncSession = Depends(deps.get_db),
                    redis=Depends(get_inbound_redis)) -> Response:
    """PagerDuty v3 webhook. Acts on `incident.acknowledged` only; everything else is acknowledged and dropped."""
    integ = await _integration(db, inbound_key)
    secret = _secret(integ.pagerduty_webhook_secret_enc)
    if secret is None:
        raise _not_found()
    tenant_id, integration_id = integ.tenant_id, integ.id
    await db.close()  # nothing below needs the database; don't hold a connection while reading the body
    await _rate_limit(redis, integration_id)
    raw = await _body(request)
    if not _pd_signature_ok(secret, raw, request.headers.get("x-pagerduty-signature", "")):
        logger.warning("rejected PagerDuty ack for tenant %s: bad signature", tenant_id)
        raise HTTPException(status_code=401, detail="invalid signature")
    event = _obj(_json(raw).get("event"))
    if event.get("event_type") != "incident.acknowledged":
        return Response(status_code=202)
    data = _obj(event.get("data"))
    agent = _str(_obj(event.get("agent")).get("id"), 32)
    agent = agent if agent and _PD_ID_RE.fullmatch(agent) else None
    incident_key = data.get("incident_key")
    if incident_key is not None:
        if parse_dedup_key(incident_key) is None:  # not an incident we opened
            return Response(status_code=202)
        _enqueue(tenant_id, "pagerduty", incident_key, agent)
        return Response(status_code=202)
    incident_id = _str(data.get("id"), 32)
    if incident_id and _PD_ID_RE.fullmatch(incident_id):
        _enqueue(tenant_id, "pagerduty", None, agent, incident_id=incident_id)
    return Response(status_code=202)


@router.post("/{inbound_key}/opsgenie", status_code=202)
async def opsgenie(inbound_key: str, request: Request, db: AsyncSession = Depends(deps.get_db),
                   redis=Depends(get_inbound_redis)) -> Response:
    """Opsgenie outgoing webhook with our token in `X-SocHub-Token`. Acts on `Acknowledge` only."""
    integ = await _integration(db, inbound_key)
    token = _secret(integ.opsgenie_token_enc)
    if token is None:
        raise _not_found()
    tenant_id, integration_id = integ.tenant_id, integ.id
    await db.close()
    await _rate_limit(redis, integration_id)
    raw = await _body(request)
    if not hmac.compare_digest(request.headers.get("x-sochub-token", "").encode(), token.encode()):
        logger.warning("rejected Opsgenie ack for tenant %s: bad token", tenant_id)
        raise HTTPException(status_code=401, detail="invalid token")
    payload = _json(raw)
    if payload.get("action") not in ("Acknowledge", "AcknowledgeAlert"):
        return Response(status_code=202)
    alert = _obj(payload.get("alert"))
    alias = alert.get("alias")
    if parse_dedup_key(alias) is None:
        return Response(status_code=202)
    _enqueue(tenant_id, "opsgenie", alias, _str(alert.get("username"), 320))
    return Response(status_code=202)
