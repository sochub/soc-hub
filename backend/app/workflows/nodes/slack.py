from app.services.slack_service import SlackError, case_buttons_block, load_integration, slack_call
from app.workflows.nodes import NodeContext, NodeError, executor, load_target_case


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
