from types import SimpleNamespace

import asyncio

import httpx
import pytest

from app.workflows.nodes import NodeContext, RetryableNodeError
from app.workflows.nodes import http as http_node


class _Res:
    def scalars(self):
        return self

    def first(self):
        return SimpleNamespace(workflow_http_allowlist=[])


class _DB:
    async def execute(self, *_a, **_k):
        return _Res()


def _nctx():
    return NodeContext(db=_DB(), run=SimpleNamespace(tenant_id=1), step=None, node={}, ctx={})


@pytest.fixture
def mock_http(monkeypatch):
    def install(handler):
        real = httpx.AsyncClient
        monkeypatch.setattr(http_node.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
        monkeypatch.setattr(http_node, "assert_url_allowed", lambda url, allow: None)
    return install


CFG = {"method": "GET", "url": "https://example.com/x"}


def test_large_body_truncated(mock_http):
    mock_http(lambda r: httpx.Response(200, content=b"a" * 2_000_000))
    out = asyncio.run(http_node.run_http(_nctx(), CFG))
    assert len(out["body"]) == http_node.MAX_BODY
    assert out["truncated"] is True


def test_500_is_retryable(mock_http):
    mock_http(lambda r: httpx.Response(500))
    with pytest.raises(RetryableNodeError):
        asyncio.run(http_node.run_http(_nctx(), CFG))


def test_404_returned(mock_http):
    mock_http(lambda r: httpx.Response(404, text="nope"))
    out = asyncio.run(http_node.run_http(_nctx(), CFG))
    assert out["status"] == 404 and "truncated" not in out


def test_json_parsed(mock_http):
    mock_http(lambda r: httpx.Response(200, json={"a": 1}))
    out = asyncio.run(http_node.run_http(_nctx(), CFG))
    assert out["body"] == {"a": 1}
