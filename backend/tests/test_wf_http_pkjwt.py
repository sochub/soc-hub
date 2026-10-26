"""http_request auth=oauth2_private_key_jwt: client credentials with a signed client assertion (RFC 7523).
The private key, the assertion and the access token never leave the node."""
import asyncio
import json
import logging
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from app.secrets.refs import placeholder
from app.workflows.nodes import NodeError
from app.workflows.nodes import http as http_node
from app.workflows.validation import workflow_errors
from tests import test_wf_http_oauth as base

TOKEN = "okta-SECRET-access-token"
TOKEN_URL = "https://login.example.com/oauth2/v1/token"
_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
RSA_PEM = _RSA.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
_EC = ec.generate_private_key(ec.SECP256R1())
EC_PEM = _EC.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
N = base.N
CFG = {"method": "GET", "url": "https://api.example.com/api/v1/logs",
       "auth": {"type": "oauth2_private_key_jwt", "token_url": TOKEN_URL, "client_id": "0oa-client",
                "private_key": placeholder("CS", N), "scope": "okta.logs.read", "key_id": "kid-cfg"}}
HOSTS = ["login.example.com", "api.example.com"]
GENERIC = "auth.private_key is not a valid PEM or JWK private key"
UNSUPPORTED = "auth.private_key: unsupported key type"


def _run(monkeypatch, hosts, body, key=RSA_PEM):
    monkeypatch.setattr(base, "CS", key)  # the secret value stored by base._with_secret
    asyncio.run(base._with_secret(hosts, body))


def _token_handler(seen):
    def handler(req):
        seen.append(req)
        if req.url.host == "login.example.com":
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": "Bearer", "expires_in": 3600})
        return httpx.Response(200, json={"echo": dict(req.headers)}, headers={"X-Echo": req.headers["authorization"]})
    return handler


def test_pkjwt_flow_rsa_pem(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    async def body(nctx):
        base._install(monkeypatch, _token_handler(seen))
        out = await http_node.run_http(nctx, CFG)
        tok_req, api_req = seen
        assert str(tok_req.url) == TOKEN_URL and tok_req.method == "POST"
        form = parse_qs(tok_req.content.decode())
        assert set(form) == {"grant_type", "scope", "client_assertion_type", "client_assertion"}
        assert form["grant_type"] == ["client_credentials"] and form["scope"] == ["okta.logs.read"]
        assert form["client_assertion_type"] == ["urn:ietf:params:oauth:client-assertion-type:jwt-bearer"]
        assertion = form["client_assertion"][0]
        hdr = jwt.get_unverified_header(assertion)
        assert hdr["alg"] == "RS256" and hdr["kid"] == "kid-cfg"
        c = jwt.decode(assertion, _RSA.public_key(), algorithms=["RS256"], audience=TOKEN_URL)
        assert c["iss"] == c["sub"] == "0oa-client" and c["exp"] - c["iat"] == 300 and c["jti"]
        assert api_req.headers["authorization"] == f"Bearer {TOKEN}"
        dumped = json.dumps(out) + str(out)
        for s in (TOKEN, assertion, "PRIVATE KEY"):
            assert s not in dumped and s not in caplog.text
        # cached: no second token request; and each assertion is unique (jti)
        await http_node.run_http(nctx, CFG)
        assert [r.url.host for r in seen].count("login.example.com") == 1
        await http_node.run_http(nctx, {**CFG, "auth": {**CFG["auth"], "scope": "okta.users.read"}})
        toks = [r for r in seen if r.url.host == "login.example.com"]
        assert len(toks) == 2  # other scope, other token
        a2 = parse_qs(toks[1].content.decode())["client_assertion"][0]
        assert jwt.decode(a2, options={"verify_signature": False})["jti"] != c["jti"]

    _run(monkeypatch, HOSTS, body)


def test_pkjwt_ec_key_es256(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, _token_handler(seen))
        await http_node.run_http(nctx, CFG)
        assertion = parse_qs(seen[0].content.decode())["client_assertion"][0]
        assert jwt.get_unverified_header(assertion)["alg"] == "ES256"
        jwt.decode(assertion, _EC.public_key(), algorithms=["ES256"], audience=TOKEN_URL)

    _run(monkeypatch, HOSTS, body, key=EC_PEM)


def test_pkjwt_pkcs1_rsa_pem(monkeypatch):
    seen = []
    pkcs1 = _RSA.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                               serialization.NoEncryption()).decode()

    async def body(nctx):
        base._install(monkeypatch, _token_handler(seen))
        await http_node.run_http(nctx, CFG)
        assertion = parse_qs(seen[0].content.decode())["client_assertion"][0]
        jwt.decode(assertion, _RSA.public_key(), algorithms=["RS256"], audience=TOKEN_URL)

    _run(monkeypatch, HOSTS, body, key=pkcs1)


def test_pkjwt_jwk_from_okta(monkeypatch):
    seen = []
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_RSA))
    jwk.pop("alg", None)
    jwk["kid"] = "okta-kid-1"
    assert "d" in jwk
    cfg = {**CFG, "auth": {k: v for k, v in CFG["auth"].items() if k != "key_id"}}

    async def body(nctx):
        base._install(monkeypatch, _token_handler(seen))
        await http_node.run_http(nctx, cfg)
        assertion = parse_qs(seen[0].content.decode())["client_assertion"][0]
        hdr = jwt.get_unverified_header(assertion)
        assert hdr["alg"] == "RS256" and hdr["kid"] == "okta-kid-1"
        jwt.decode(assertion, _RSA.public_key(), algorithms=["RS256"], audience=TOKEN_URL)

    _run(monkeypatch, HOSTS, body, key=json.dumps(jwk))


@pytest.mark.parametrize("bad", [
    "-----BEGIN PRIVATE KEY-----\nSECRETSTUFF\n-----END PRIVATE KEY-----\n",
    '{"kty": "RSA", "d": "SECRETSTUFF", "n": "x"}',
    '{"kty": "RSA", "n": "SECRETSTUFF", "e": "AQAB"}',  # public JWK only
    "SECRETSTUFF",
])
def test_pkjwt_bad_key_generic_error(monkeypatch, caplog, bad):
    caplog.set_level(logging.DEBUG)
    seen = []

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert str(ei.value) == GENERIC
        assert "SECRETSTUFF" not in str(ei.value) and "SECRETSTUFF" not in caplog.text and seen == []

    _run(monkeypatch, HOSTS, body, key=bad)


@pytest.mark.parametrize("make", [
    lambda: ed25519.Ed25519PrivateKey.generate(),
    lambda: ec.generate_private_key(ec.SECP384R1()),
])
def test_pkjwt_unsupported_key(monkeypatch, make):
    seen = []
    pem = make().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert str(ei.value) == UNSUPPORTED and seen == []

    _run(monkeypatch, HOSTS, body, key=pem)


def test_pkjwt_key_not_bound_to_api_host(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert "not allowed for host api.example.com" in str(ei.value) and seen == []

    _run(monkeypatch, ["login.example.com"], body)


def test_pkjwt_key_not_bound_to_token_host(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        assert "not allowed for host login.example.com" in str(ei.value) and seen == []

    _run(monkeypatch, ["api.example.com"], body)


def test_pkjwt_plaintext_key_rejected_at_run(monkeypatch):
    seen = []
    cfg = {**CFG, "auth": {**CFG["auth"], "private_key": RSA_PEM}}

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200, json={}))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, cfg)
        assert str(ei.value) == "auth.private_key must be a secret reference like {{ secrets.NAME }}"
        assert seen == []

    _run(monkeypatch, HOSTS, body)


def test_pkjwt_token_failure_hides_assertion(monkeypatch):
    def handler(req):
        raise httpx.ConnectError(f"boom {req.content.decode()}")

    async def body(nctx):
        base._install(monkeypatch, handler)
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        msg = str(ei.value)
        assert msg.startswith("token request failed") and "eyJ" not in msg and "PRIVATE" not in msg

    _run(monkeypatch, HOSTS, body)


def test_pkjwt_validation():
    def msgs(auth):
        g = {"nodes": [{"id": "t", "type": "trigger", "config": {}},
                       {"id": "h", "type": "http_request", "config": {"url": "https://a.example.com", "auth": auth}}],
             "edges": [{"source": "t", "target": "h"}]}
        return [e["message"] for e in workflow_errors(g, "manual", None, None)]

    ok = {"type": "oauth2_private_key_jwt", "token_url": "https://a.example.com/oauth2/v1/token",
          "client_id": "0oa", "private_key": "{{ secrets.OKTA_READ_PRIVATE_KEY }}", "scope": "okta.logs.read"}
    assert not [m for m in msgs(ok) if "auth" in m]
    assert not [m for m in msgs({**ok, "key_id": "k", "dpop": True}) if "auth" in m]
    assert "missing required field 'auth.scope'" in msgs({k: v for k, v in ok.items() if k != "scope"})
    assert "missing required field 'auth.private_key'" in msgs({k: v for k, v in ok.items() if k != "private_key"})
    assert ("auth.private_key must be a secret reference like {{ secrets.NAME }}"
            in msgs({**ok, "private_key": "-----BEGIN PRIVATE KEY-----"}))
