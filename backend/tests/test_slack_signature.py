import asyncio
import hashlib
import hmac
from types import SimpleNamespace

import pytest
from cryptography.fernet import InvalidToken

from app.services import slack_service
from app.services.slack_service import SlackError, load_integration, parse_action_value, verify_signature

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
BODY = b"payload=%7B%22type%22%3A%22block_actions%22%7D"
TS = "1531420618"


def sign(secret, ts, body):
    return "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()


def test_valid_signature():
    assert verify_signature(SECRET, TS, BODY, sign(SECRET, TS, BODY), now=int(TS) + 10)


def test_wrong_secret_or_tampered_body():
    assert not verify_signature(SECRET, TS, BODY, sign("other", TS, BODY), now=int(TS))
    assert not verify_signature(SECRET, TS, BODY + b"x", sign(SECRET, TS, BODY), now=int(TS))


def test_stale_timestamp_rejected():
    assert not verify_signature(SECRET, TS, BODY, sign(SECRET, TS, BODY), now=int(TS) + 301)


def test_garbage_headers_rejected():
    assert not verify_signature(SECRET, "not-a-number", BODY, "v0=abc", now=0)
    assert not verify_signature(SECRET, TS, BODY, "", now=int(TS))


def test_parse_action_value():
    assert parse_action_value("wf:tok_123-x:1") == ("wf", "tok_123-x", 1)
    assert parse_action_value("case:3:10:ack") == ("case", 3, 10, "ack")
    assert parse_action_value("case:3:10:delete") is None
    assert parse_action_value("wf:tok") is None
    assert parse_action_value("junk") is None
    assert parse_action_value("case:a:b:ack") is None


class _FakeDB:
    def __init__(self, integ):
        self._integ = integ

    async def execute(self, _stmt):
        integ = self._integ
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: integ))


def test_load_integration_not_configured():
    with pytest.raises(SlackError, match="not configured"):
        asyncio.run(load_integration(_FakeDB(None), 1))


def test_load_integration_undecryptable_token(monkeypatch):
    def boom(_token):
        raise InvalidToken()

    monkeypatch.setattr(slack_service, "decrypt", boom)
    with pytest.raises(SlackError, match="can't be decrypted"):
        asyncio.run(load_integration(_FakeDB(SimpleNamespace(bot_token_enc="x")), 1))


def test_signature_non_ascii_or_bytes_fail_closed():
    assert verify_signature(SECRET, TS, BODY, "v0=\xe9", now=int(TS)) is False
    assert verify_signature(SECRET, TS, BODY, b"v0=abc", now=int(TS)) is False


def test_parse_action_value_strict_grammar():
    assert parse_action_value("wf:tok:1\n") is None
    assert parse_action_value("wf:tok:١") is None
    assert parse_action_value("case:1:99999999999:ack") is None


@pytest.mark.parametrize("url", [
    "http://backend:8000/x",
    "https://evil.com/",
    "https://hooks.slack.com.evil.com/",
    "http://hooks.slack.com/actions/T/1/x",
])
def test_respond_refuses_non_slack_url(url):
    with pytest.raises(SlackError, match="non-Slack response_url"):
        asyncio.run(slack_service.respond(url, {"text": "x"}))


import httpx

_REAL_CLIENT = httpx.AsyncClient


def _patch_client(monkeypatch, handler):
    real = _REAL_CLIENT
    monkeypatch.setattr(slack_service.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_respond_posts_to_slack(monkeypatch):
    import httpx
    seen = []

    def handler(req):
        seen.append((str(req.url), req.content))
        return httpx.Response(200, text="ok")

    _patch_client(monkeypatch, handler)
    asyncio.run(slack_service.respond("https://hooks.slack.com/actions/T/1/x", {"text": "hi"}))
    assert seen[0][0] == "https://hooks.slack.com/actions/T/1/x"


def test_respond_wraps_errors(monkeypatch):
    import httpx
    _patch_client(monkeypatch, lambda req: httpx.Response(500))
    with pytest.raises(SlackError):
        asyncio.run(slack_service.respond("https://hooks.slack.com/actions/T/1/x", {}))

    def boom(req):
        raise httpx.ConnectError("down")

    _patch_client(monkeypatch, boom)
    with pytest.raises(SlackError):
        asyncio.run(slack_service.respond("https://hooks.slack.com/actions/T/1/x", {}))
