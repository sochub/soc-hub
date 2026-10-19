"""http_request auth=oauth2_client_credentials: token never leaves the node; client secret is host-bound."""
import asyncio
import json
import logging
import secrets as pysecrets
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import delete

import app.db.base  # noqa: F401
from app.db.session import AsyncSessionLocal, engine
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.secrets.refs import placeholder
from app.utils.crypto import encrypt
from app.workflows.nodes import NodeContext, NodeError
from app.workflows.nodes import http as http_node

TOKEN = "tok-SECRET-123"
CS = "client-SECRET-xyz"
N = "0123456789abcdef"
_REAL = httpx.AsyncClient


def _install(monkeypatch, handler):
    monkeypatch.setattr(http_node.httpx, "AsyncClient", lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(http_node, "assert_url_allowed", lambda url, allow: None)
    http_node._TOKENS.clear()


async def _with_secret(allowed_hosts, body):
    await engine.dispose()
    ids = {"t": None, "s": None}
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{pysecrets.token_hex(3)}-oa")
            db.add(t)
            await db.flush()
            ids["t"] = t.id
            s = TenantSecret(tenant_id=t.id, name="CS", value_enc=encrypt(CS), allowed_hosts=allowed_hosts)
            db.add(s)
            await db.flush()
            ids["s"] = s.id
            await db.commit()
            nctx = NodeContext(db=db, run=SimpleNamespace(tenant_id=t.id), step=None, node={}, ctx={},
                               secret_nonce=N, secret_names={"CS"})
            await body(nctx)
    finally:
        async with AsyncSessionLocal() as db:
            if ids["s"]:
                await db.execute(delete(TenantSecret).where(TenantSecret.id == ids["s"]))
            if ids["t"]:
                await db.execute(delete(Tenant).where(Tenant.id == ids["t"]))
            await db.commit()
        await engine.dispose()


CFG = {"method": "GET", "url": "https://api.example.com/v1/alerts", "headers": {"Authorization": "Basic x"},
       "auth": {"type": "oauth2_client_credentials", "token_url": "https://login.example.com/oauth2/token",
                "client_id": "cid", "client_secret": placeholder("CS", N), "scope": "alerts.read"}}


def test_token_flow_and_redaction(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.host == "login.example.com":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        return httpx.Response(200, json={"echo": dict(req.headers)}, headers={"X-Echo": req.headers["authorization"]})

    async def body(nctx):
        _install(monkeypatch, handler)
        out = await http_node.run_http(nctx, CFG)
        tok_req, api_req = seen
        assert tok_req.method == "POST" and tok_req.headers["content-type"] == "application/x-www-form-urlencoded"
        assert parse_qs(tok_req.content.decode()) == {"grant_type": ["client_credentials"], "client_id": ["cid"],
                                                      "client_secret": [CS], "scope": ["alerts.read"]}
        assert api_req.headers["authorization"] == f"Bearer {TOKEN}"
        dumped = json.dumps(out) + str(out)
        assert TOKEN not in dumped and CS not in dumped
        # cached: second call makes no token request
        await http_node.run_http(nctx, CFG)
        assert [r.url.host for r in seen] == ["login.example.com", "api.example.com", "api.example.com"]
        assert TOKEN not in caplog.text and CS not in caplog.text

    asyncio.run(_with_secret(["api.example.com", "login.example.com"], body))


def test_api_error_text_has_no_token(monkeypatch):
    def handler(req):
        if req.url.host == "login.example.com":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        raise httpx.ConnectError(f"boom {req.headers['authorization']}")

    async def body(nctx):
        _install(monkeypatch, handler)
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert TOKEN not in str(ei.value)

    asyncio.run(_with_secret(["api.example.com", "login.example.com"], body))


def test_token_401_error_hides_body(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(401, json={"error": "invalid_client", "echo": CS})

    async def body(nctx):
        _install(monkeypatch, handler)
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert str(ei.value) == "token request failed: HTTP 401"
        assert len(seen) == 1  # no API call

    asyncio.run(_with_secret(["api.example.com", "login.example.com"], body))


def test_secret_not_bound_to_token_host(monkeypatch):
    seen = []

    async def body(nctx):
        _install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={"access_token": TOKEN}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert "not allowed for host login.example.com" in str(ei.value)
        assert seen == []

    asyncio.run(_with_secret(["api.example.com"], body))


def test_token_bound_to_client_secret_hosts(monkeypatch):
    """A secret bound only to the token host must not mint a token for some other API host."""
    seen = []

    async def body(nctx):
        _install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={"access_token": TOKEN}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert "not allowed for host api.example.com" in str(ei.value) and TOKEN not in str(ei.value)
        assert seen == []  # refused before the token request

    asyncio.run(_with_secret(["login.example.com"], body))


def test_validation_rejects_literal_client_secret():
    from app.workflows.validation import validate_graph
    def g(secret):
        return {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                          {"id": "h", "type": "http_request", "config": {
                              "method": "GET", "url": "https://api.example.com/x",
                              "auth": {"type": "oauth2_client_credentials", "token_url": "https://login.example.com/t",
                                       "client_id": "{{ secrets.CID }}", "client_secret": secret}}}],
                "edges": [{"source": "start", "target": "h"}]}
    msg = "auth.client_secret must be a secret reference like {{ secrets.NAME }}"
    assert msg in [e["message"] for e in validate_graph(g("hunter2"), "manual")]
    assert msg in [e["message"] for e in validate_graph(g("{{ secrets.SEC }}x"), "manual")]
    assert msg not in [e["message"] for e in validate_graph(g("{{ secrets.SEC }}"), "manual")]


def test_audience_sent_in_token_request(monkeypatch):
    seen = []

    async def body(nctx):
        def handler(req):
            seen.append(req)
            if "oauth2/token" in str(req.url):
                return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
            return httpx.Response(200, json={})
        _install(monkeypatch, handler)
        cfg = {**CFG, "auth": {**CFG["auth"], "audience": "wiz-api"}}
        await http_node.run_http(nctx, cfg)
        assert "audience=wiz-api" in seen[0].content.decode()

    asyncio.run(_with_secret(["api.example.com", "login.example.com"], body))
