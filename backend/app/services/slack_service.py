"""Slack Web API client (httpx, form-encoded), request verification and Block Kit builders."""
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlsplit
from typing import List, Optional, Tuple

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import select

from app.models.slack_integration import SlackIntegration
from app.utils.crypto import decrypt

SLACK_API = "https://slack.com/api/"
_CASE_ACTIONS = {"ack", "assign", "close"}
_WF_RE = re.compile(r"wf:([A-Za-z0-9_\-]{1,64}):([0-9]{1,2})")
_CASE_RE = re.compile(r"case:([0-9]{1,10}):([0-9]{1,10}):([a-z]{1,10})")


class SlackError(Exception):
    pass


def verify_signature(signing_secret: str, timestamp: str, body: bytes, signature: str, now: Optional[float] = None) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - ts) > 300:
        return False
    expected = "v0=" + hmac.new(signing_secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    try:
        sig = signature.encode("utf-8", "surrogateescape") if isinstance(signature, str) else b""
        return hmac.compare_digest(expected.encode(), sig)
    except (TypeError, ValueError):
        return False


async def slack_call(token: str, method: str, **args) -> dict:
    data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in args.items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(SLACK_API + method, data=data, headers={"Authorization": f"Bearer {token}"})
        body = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SlackError(f"{method}: {e}") from e
    if not body.get("ok"):
        raise SlackError(f"{method}: {body.get('error', 'unknown_error')}")
    return body


async def respond(response_url: str, payload: dict) -> None:
    parts = urlsplit(response_url or "")
    if parts.scheme != "https" or parts.hostname != "hooks.slack.com":
        raise SlackError("refusing non-Slack response_url")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(response_url, json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SlackError(f"response_url: {e}") from e


def parse_action_value(value: str) -> Optional[Tuple]:
    m = _WF_RE.fullmatch(value or "")
    if m:
        return ("wf", m.group(1), int(m.group(2)))
    m = _CASE_RE.fullmatch(value or "")
    if m and m.group(3) in _CASE_ACTIONS:
        return ("case", int(m.group(1)), int(m.group(2)), m.group(3))
    return None


def case_buttons_block(tenant_id: int, case_id: int) -> dict:
    def btn(text, action, style=None):
        b = {"type": "button", "text": {"type": "plain_text", "text": text},
             "action_id": f"case_{action}", "value": f"case:{tenant_id}:{case_id}:{action}"}
        if style:
            b["style"] = style
        return b
    return {"type": "actions", "elements": [btn("Acknowledge", "ack", "primary"), btn("Assign to me", "assign"), btn("Close", "close", "danger")]}


def ask_blocks(message: str, buttons: List[str], wait_token: str) -> List[dict]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": message}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": label[:75]},
             "action_id": f"wf_answer_{i}", "value": f"wf:{wait_token}:{i}"}
            for i, label in enumerate(buttons)
        ]},
    ]


async def load_integration(db, tenant_id: int) -> Tuple[SlackIntegration, str]:
    integ = (await db.execute(select(SlackIntegration).where(SlackIntegration.tenant_id == tenant_id))).scalars().first()
    if not integ:
        raise SlackError("Slack is not configured for this tenant")
    try:
        return integ, decrypt(integ.bot_token_enc)
    except InvalidToken as e:
        raise SlackError("Slack credentials can't be decrypted (SECRET_KEY changed?) — re-enter them in Integrations") from e


BUTTONS_TYPE_ERROR = "buttons must be a comma-separated string or a list"


def parse_button_labels(raw) -> list:
    """Normalize a `buttons` config value to a list of labels. Raises ValueError on a bad type."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    elif not isinstance(raw, (list, tuple)):
        raise ValueError(BUTTONS_TYPE_ERROR)
    return [str(b).strip() for b in raw if str(b).strip()]
