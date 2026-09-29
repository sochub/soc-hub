from types import SimpleNamespace

import asyncio

import httpx
import socket

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


def test_request_pinned_to_vetted_ip(monkeypatch):
    """DNS rebinding: the name resolves public for the guard, private afterwards. The request must
    go to the vetted IP while keeping the hostname for the Host header and TLS SNI/verification."""
    from app.workflows import ssrf
    answers = iter(["93.184.216.34", "10.0.0.5"])

    def rebinding(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient
    monkeypatch.setattr(http_node.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(http_node, "assert_url_allowed",
                        lambda url, allow: ssrf.assert_url_allowed(url, allow, resolve=rebinding))
    cfg = {"method": "POST", "url": "https://example.com:8443/hook?a=1", "headers": {"HOST": "evil.internal", "X-A": "1"}}
    out = asyncio.run(http_node.run_http(_nctx(), cfg))
    assert out["status"] == 200
    (req,) = seen
    assert req.url.host == "93.184.216.34" and req.url.port == 8443 and req.url.raw_path == b"/hook?a=1"
    assert req.headers["host"] == "example.com:8443" and req.headers.get_list("host") == ["example.com:8443"]
    assert req.headers["x-a"] == "1"
    assert req.extensions["sni_hostname"] == "example.com"
    assert next(answers) == "10.0.0.5", "the name must be resolved exactly once"


def test_pinned_ipv6_url(monkeypatch):
    seen = []
    real = httpx.AsyncClient
    monkeypatch.setattr(http_node.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(204)), **kw))
    monkeypatch.setattr(http_node, "assert_url_allowed", lambda url, allow: "2606:2800:220:1:248:1893:25c8:1946")
    asyncio.run(http_node.run_http(_nctx(), {"method": "GET", "url": "http://v6.example.com/x"}))
    assert seen[0].url.host == "2606:2800:220:1:248:1893:25c8:1946" and seen[0].headers["host"] == "v6.example.com"
