"""http_request auth=google_service_account: JWT-bearer grant at a fixed token URL; key, JWT and token stay inside."""
import asyncio
import json
import logging
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.secrets.refs import placeholder
from app.workflows.nodes import NodeError
from app.workflows.nodes import http as http_node
from app.workflows.validation import workflow_errors
from tests import test_wf_http_oauth as base

TOKEN = "ya29.SECRET-token"
_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = _PRIV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
KEY = json.dumps({"type": "service_account", "client_email": "svc@proj.iam.gserviceaccount.com",
                  "private_key_id": "kid-123", "private_key": PEM, "token_uri": "https://evil.example.com/token"})
N = base.N
CFG = {"method": "GET", "url": "https://alertcenter.googleapis.com/v1beta1/alerts",
       "auth": {"type": "google_service_account", "service_account_key": placeholder("CS", N),
                "subject": "admin@corp.example", "scopes": "https://www.googleapis.com/auth/apps.alerts"}}
HOSTS = ["oauth2.googleapis.com", "alertcenter.googleapis.com"]


def _run(monkeypatch, hosts, body, key=KEY):
    monkeypatch.setattr(base, "CS", key)  # the secret value stored by base._with_secret
    asyncio.run(base._with_secret(hosts, body))


def test_jwt_flow_cache_and_redaction(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})
        return httpx.Response(200, json={"echo": dict(req.headers)})

    async def body(nctx):
        base._install(monkeypatch, handler)
        out = await http_node.run_http(nctx, CFG)
        tok_req, api_req = seen
        assert str(tok_req.url) == "https://oauth2.googleapis.com/token"  # not the key's token_uri
        form = parse_qs(tok_req.content.decode())
        assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        assertion = form["assertion"][0]
        assert jwt.get_unverified_header(assertion)["kid"] == "kid-123"
        c = jwt.decode(assertion, _PRIV.public_key(), algorithms=["RS256"], audience="https://oauth2.googleapis.com/token")
        assert c["iss"] == "svc@proj.iam.gserviceaccount.com" and c["sub"] == "admin@corp.example"
        assert c["scope"] == "https://www.googleapis.com/auth/apps.alerts" and c["exp"] - c["iat"] == 3600
        assert api_req.headers["authorization"] == f"Bearer {TOKEN}"
        dumped = json.dumps(out)
        for s in (TOKEN, assertion, "PRIVATE KEY", "kid-123"):
            assert s not in dumped and s not in caplog.text
        await http_node.run_http(nctx, CFG)  # cached
        await http_node.run_http(nctx, {**CFG, "auth": {**CFG["auth"], "subject": "other@corp.example"}})
        assert [r.url.host for r in seen].count("oauth2.googleapis.com") == 2  # other subject, other token

    _run(monkeypatch, HOSTS, body)


def test_token_failure_hides_key_and_jwt(monkeypatch):
    sent = []

    def handler(req):
        sent.append(req.content.decode())
        raise httpx.ConnectError(f"boom {req.content.decode()}")

    async def body(nctx):
        base._install(monkeypatch, handler)
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        msg = str(ei.value)
        assert msg.startswith("token request failed") and "assertion" not in msg and "eyJ" not in msg

    _run(monkeypatch, HOSTS, body)


def test_malformed_key_generic_error(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert str(ei.value) == "auth.service_account_key is not a valid service account JSON key"
        assert seen == []

    _run(monkeypatch, HOSTS, body, key='{"client_email": "x@y", "private_key": "-----BEGIN SECRETSTUFF"}')


def test_key_not_bound_to_token_host(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert "not allowed for host oauth2.googleapis.com" in str(ei.value) and seen == []

    _run(monkeypatch, ["alertcenter.googleapis.com"], body)


def test_token_bound_to_key_hosts(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert "not allowed for host alertcenter.googleapis.com" in str(ei.value) and seen == []

    _run(monkeypatch, ["oauth2.googleapis.com"], body)


def test_validation():
    def msgs(auth):
        g = {"nodes": [{"id": "t", "type": "trigger", "config": {}},
                       {"id": "h", "type": "http_request", "config": {"url": "https://a.example.com", "auth": auth}}],
             "edges": [{"source": "t", "target": "h"}]}
        return [e["message"] for e in workflow_errors(g, "manual", None, None)]

    ok = {"type": "google_service_account", "service_account_key": "{{ secrets.GWS_SA_KEY }}", "scopes": "s"}
    assert not [m for m in msgs(ok) if "auth" in m]
    assert any("service_account_key must be a secret reference" in m for m in msgs({**ok, "service_account_key": "{}"}))
    assert any("auth.scopes" in m for m in msgs({k: v for k, v in ok.items() if k != "scopes"}))
