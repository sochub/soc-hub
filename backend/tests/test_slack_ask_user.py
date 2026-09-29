from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import slack_service
from app.services.slack_service import SlackError, parse_action_value
from app.workflows.nodes import WAIT, NodeError, slack as node


def make(monkeypatch, responses):
    calls = []

    async def fake_call(token, method, **kw):
        calls.append((method, kw))
        r = responses[method]
        if isinstance(r, Exception):
            raise r
        return r

    async def fake_integ(nctx):
        return object(), "xoxb-test"

    monkeypatch.setattr(node, "slack_call", fake_call)
    monkeypatch.setattr(node, "_integration", fake_integ)
    nctx = SimpleNamespace(db=None, run=SimpleNamespace(tenant_id=1), step=SimpleNamespace(), node={}, ctx={})
    return nctx, calls


OK = {
    "users.lookupByEmail": {"user": {"id": "U1"}},
    "conversations.open": {"channel": {"id": "D1"}},
    "chat.postMessage": {"ts": "1.2"},
}


@pytest.mark.asyncio
async def test_ask_user_sends_and_waits(monkeypatch):
    nctx, calls = make(monkeypatch, OK)
    before = datetime.now(timezone.utc)
    res = await node.run_ask_user(nctx, {"email": " a@b.c ", "message": "ok?", "buttons": "Yes, No", "timeout_hours": 2})
    assert res is WAIT
    assert [c[0] for c in calls] == ["users.lookupByEmail", "conversations.open", "chat.postMessage"]
    assert calls[0][1] == {"email": "a@b.c"}
    tok = nctx.step.wait_token
    assert calls[2][1]["blocks"] == slack_service.ask_blocks("ok?", ["Yes", "No"], tok)
    assert len(tok) == 32
    assert parse_action_value(f"wf:{tok}:0") == ("wf", tok, 0)
    delta = nctx.step.wait_expires_at - before
    assert timedelta(hours=2) <= delta < timedelta(hours=2, seconds=5)
    assert nctx.step.output == {"target_slack_id": "U1", "channel": "D1", "message_ts": "1.2", "buttons": ["Yes", "No"]}


@pytest.mark.asyncio
async def test_ask_user_unknown_email(monkeypatch):
    nctx, _ = make(monkeypatch, {**OK, "users.lookupByEmail": SlackError("users_not_found")})
    with pytest.raises(NodeError, match="x@y.z"):
        await node.run_ask_user(nctx, {"email": "x@y.z", "message": "hi"})


@pytest.mark.asyncio
async def test_ask_user_too_many_buttons(monkeypatch):
    nctx, calls = make(monkeypatch, OK)
    with pytest.raises(NodeError, match="5 buttons"):
        await node.run_ask_user(nctx, {"email": "a@b.c", "message": "hi", "buttons": "a,b,c,d,e,f"})
    assert calls == []
