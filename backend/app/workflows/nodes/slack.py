import secrets
from datetime import datetime, timedelta, timezone

from app.services.slack_service import SlackError, ask_blocks, case_buttons_block, load_integration, parse_button_labels, slack_call
from app.workflows.nodes import WAIT, NodeContext, NodeError, executor, load_target_case


async def _integration(nctx: NodeContext):
    try:
        return await load_integration(nctx.db, nctx.run.tenant_id)
    except SlackError as e:
        raise NodeError(str(e))


@executor("slack_post_message")
async def run_post_message(nctx: NodeContext, config: dict) -> dict:
    integ, token = await _integration(nctx)
    channel = config.get("channel") or integ.default_channel
    if not channel:
        raise NodeError("no channel given and no default channel configured")
    text = str(config["text"])
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if str(config.get("include_case_buttons", "no")).lower() in ("yes", "true", "1"):
        case = await load_target_case(nctx, config)
        blocks.append(case_buttons_block(nctx.run.tenant_id, case.id))
    try:
        res = await slack_call(token, "chat.postMessage", channel=channel, text=text, blocks=blocks)
    except SlackError as e:
        raise NodeError(str(e))
    return {"channel": res["channel"], "ts": res["ts"]}


def button_labels(raw) -> list:
    try:
        labels = parse_button_labels(raw)
    except ValueError as e:
        raise NodeError(str(e))
    return labels or ["Yes", "No"]


@executor("slack_ask_user")
async def run_ask_user(nctx: NodeContext, config: dict):
    email = str(config["email"]).strip()
    message = str(config["message"])
    if not email:
        raise NodeError("email is empty — check the template")
    if not message.strip():
        raise NodeError("message is empty — check the template")
    _, token = await _integration(nctx)
    buttons = button_labels(config.get("buttons"))
    if len(buttons) > 5:
        raise NodeError("at most 5 buttons")
    raw_hours = config.get("timeout_hours")
    try:
        hours = 24.0 if raw_hours in (None, "") else float(raw_hours)
        valid = not isinstance(raw_hours, bool) and 0 < hours <= 168
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise NodeError("timeout_hours must be between 0 and 168")
    wait_token = secrets.token_urlsafe(24)
    try:
        user_id = (await slack_call(token, "users.lookupByEmail", email=email))["user"]["id"]
        channel = (await slack_call(token, "conversations.open", users=user_id))["channel"]["id"]
        msg = await slack_call(token, "chat.postMessage", channel=channel, text=message,
                               blocks=ask_blocks(message, buttons, wait_token))
    except SlackError as e:
        raise NodeError(f"could not ask {email}: {e}")
    nctx.step.wait_token = wait_token
    nctx.step.wait_expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    nctx.step.output = {"target_slack_id": user_id, "channel": channel, "message_ts": msg["ts"], "buttons": buttons}
    return WAIT
