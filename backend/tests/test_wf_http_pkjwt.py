"""http_request auth=oauth2_private_key_jwt: client credentials with a signed client assertion (RFC 7523).
The private key, the assertion and the access token never leave the node."""
import asyncio
import base64
import hashlib
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
    """A token-call error whose text quotes the request body goes through redact_text: no assertion."""
    sent = []
    real_send = http_node._send

    async def fake_send(url, method, headers, body, pinned_ip, secret=False):
        if "login.example.com" in url:
            sent.append(body)
            raise NodeError(f"boom {body}")
        return await real_send(url, method, headers, body, pinned_ip, secret)

    async def body(nctx):
        base._install(monkeypatch, lambda req: httpx.Response(200, json={}))
        monkeypatch.setattr(http_node, "_send", fake_send)
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, CFG)
        msg = str(ei.value)
        assertion = parse_qs(sent[0])["client_assertion"][0]
        assert assertion.count(".") == 2 and len(assertion) > 100
        assert msg.startswith("token request failed: boom") and assertion not in msg
        assert "PRIVATE" not in msg

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
    for bad in ("true", 1, "yes", None):
        assert "auth.dpop must be true or false" in msgs({**ok, "dpop": bad})
    for good in (True, False):
        assert "auth.dpop must be true or false" not in msgs({**ok, "dpop": good})


# ---- DPoP (RFC 9449) ----

DCFG = {**CFG, "url": "https://api.example.com/api/v1/logs?q=1#frag", "auth": {**CFG["auth"], "dpop": True}}
NOT_DPOP = "token is not DPoP-bound — enable Require DPoP on the app or set dpop: false"


def _claims(proof):
    return jwt.decode(proof, options={"verify_signature": False})


def _thumb(jwk):
    return json.dumps({k: jwk[k] for k in ("crv", "kty", "x", "y")}, sort_keys=True, separators=(",", ":"))


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _dpop_handler(seen, api=None, token_type="DPoP"):
    """Token endpoint: nonce challenge first (no nonce claim), then a DPoP token. API: `api` or 200."""
    def handler(req):
        seen.append(req)
        if req.url.host == "login.example.com":
            if "dpop" not in req.headers:
                return httpx.Response(200, json={"access_token": TOKEN, "token_type": "Bearer", "expires_in": 3600})
            if "nonce" not in _claims(req.headers["dpop"]):
                return httpx.Response(400, json={"error": "use_dpop_nonce"}, headers={"DPoP-Nonce": "n1"})
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": token_type, "expires_in": 3600})
        return api(req) if api else httpx.Response(200, json={"ok": True})
    return handler


def _toks(seen):
    return [r for r in seen if r.url.host == "login.example.com"]


def _apis(seen):
    return [r for r in seen if r.url.host == "api.example.com"]


def test_dpop_token_nonce_retry(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen))
        await http_node.run_http(nctx, DCFG)
        toks = _toks(seen)
        assert len(toks) == 2
        first, second = (r.headers["dpop"] for r in toks)
        assert "nonce" not in _claims(first)
        c = _claims(second)
        assert c["nonce"] == "n1" and c["htm"] == "POST" and c["htu"] == TOKEN_URL and c["jti"] and c["iat"]
        assert "ath" not in c
        hdr = jwt.get_unverified_header(second)
        assert hdr["typ"] == "dpop+jwt" and hdr["alg"] == "ES256"
        assert set(hdr["jwk"]) == {"kty", "crv", "x", "y"} and "d" not in hdr["jwk"]
        assert hdr["jwk"]["kty"] == "EC" and hdr["jwk"]["crv"] == "P-256"
        jwt.decode(second, jwt.PyJWK(hdr["jwk"]).key, algorithms=["ES256"])  # verifies with its own jwk
        # both token requests carry a fresh client assertion and the same DPoP key
        a1, a2 = (parse_qs(r.content.decode())["client_assertion"][0] for r in toks)
        assert a1 != a2
        assert _thumb(jwt.get_unverified_header(first)["jwk"]) == _thumb(hdr["jwk"])

    _run(monkeypatch, HOSTS, body)


def test_dpop_token_nonce_retry_only_once(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(400, json={"error": "use_dpop_nonce"}, headers={"dpop-nonce": "again"})

    async def body(nctx):
        base._install(monkeypatch, handler)
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, DCFG)
        assert str(ei.value) == "token request failed: HTTP 400" and len(seen) == 2

    _run(monkeypatch, HOSTS, body)


@pytest.mark.parametrize("resp", [
    httpx.Response(400, json={"error": "use_dpop_nonce"}),  # no nonce header
    httpx.Response(400, json={"error": "invalid_client"}, headers={"DPoP-Nonce": "n1"}),
    httpx.Response(401, json={"error": "use_dpop_nonce"}, headers={"DPoP-Nonce": "n1"}),
])
def test_dpop_token_no_retry_without_full_challenge(monkeypatch, resp):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, lambda req: seen.append(req) or resp)
        with pytest.raises(NodeError):
            await http_node.run_http(nctx, DCFG)
        assert len(seen) == 1

    _run(monkeypatch, HOSTS, body)


def test_dpop_api_request_proof(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen))
        out = await http_node.run_http(nctx, DCFG)
        assert out["status"] == 200
        (api,) = _apis(seen)
        assert api.headers["authorization"] == f"DPoP {TOKEN}"
        proof = api.headers["dpop"]
        c = _claims(proof)
        assert c["htm"] == "GET" and c["htu"] == "https://api.example.com/api/v1/logs"
        assert c["ath"] == _b64(hashlib.sha256(TOKEN.encode()).digest()) and "nonce" not in c
        hdr = jwt.get_unverified_header(proof)
        assert hdr["typ"] == "dpop+jwt" and hdr["alg"] == "ES256" and "d" not in hdr["jwk"]
        jwt.decode(proof, jwt.PyJWK(hdr["jwk"]).key, algorithms=["ES256"])
        tok_jwk = jwt.get_unverified_header(_toks(seen)[-1].headers["dpop"])["jwk"]
        assert _thumb(hdr["jwk"]) == _thumb(tok_jwk)

    _run(monkeypatch, HOSTS, body)


def test_dpop_user_headers_cannot_override(monkeypatch):
    seen = []
    cfg = {**DCFG, "method": "post", "headers": {"dpop": "forged", "Authorization": "Bearer x"}}

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen))
        await http_node.run_http(nctx, cfg)
        (api,) = _apis(seen)
        assert api.headers.get_list("dpop") != ["forged"] and len(api.headers.get_list("dpop")) == 1
        assert _claims(api.headers["dpop"])["htm"] == "POST"
        assert api.headers["authorization"] == f"DPoP {TOKEN}"

    _run(monkeypatch, HOSTS, body)


def test_dpop_cached_key_reused(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen))
        await http_node.run_http(nctx, DCFG)
        await http_node.run_http(nctx, DCFG)
        assert len(_toks(seen)) == 2  # second run: no new token request
        p1, p2 = (r.headers["dpop"] for r in _apis(seen))
        assert p1 != p2 and _claims(p1)["jti"] != _claims(p2)["jti"]
        assert _thumb(jwt.get_unverified_header(p1)["jwk"]) == _thumb(jwt.get_unverified_header(p2)["jwk"])
        # dpop is part of the cache key: the same config without dpop mints a separate Bearer token
        await http_node.run_http(nctx, {**DCFG, "auth": {**DCFG["auth"], "dpop": False}})
        assert len(_toks(seen)) == 3 and _apis(seen)[-1].headers["authorization"].startswith("Bearer ")
        assert "dpop" not in _apis(seen)[-1].headers

    _run(monkeypatch, HOSTS, body)


def test_dpop_flag_must_be_real_bool_at_runtime(monkeypatch):
    seen = []

    async def body(nctx):
        base._install(monkeypatch, _token_handler(seen))
        await http_node.run_http(nctx, {**DCFG, "auth": {**DCFG["auth"], "dpop": "true"}})
        assert "dpop" not in seen[0].headers  # only `True` turns DPoP on
        assert seen[1].headers["authorization"] == f"Bearer {TOKEN}"

    _run(monkeypatch, HOSTS, body)


def test_dpop_token_type_case(monkeypatch):
    seen = []

    async def ok(nctx):
        base._install(monkeypatch, _dpop_handler(seen, token_type="dpop"))
        await http_node.run_http(nctx, DCFG)
        assert _apis(seen)[0].headers["authorization"] == f"DPoP {TOKEN}"

    _run(monkeypatch, HOSTS, ok)
    seen2 = []

    async def bad(nctx):
        base._install(monkeypatch, _dpop_handler(seen2, token_type="Bearer"))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, DCFG)
        assert str(ei.value) == NOT_DPOP and _apis(seen2) == [] and http_node._TOKENS == {}

    _run(monkeypatch, HOSTS, bad)


def test_dpop_api_nonce_retry(monkeypatch):
    seen = []

    def api(req):
        if "nonce" not in _claims(req.headers["dpop"]):
            return httpx.Response(401, json={}, headers={"WWW-Authenticate": 'DPoP error="use_dpop_nonce"',
                                                         "DPoP-Nonce": "n2"})
        return httpx.Response(200, json={"ok": True})

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen, api=api))
        out = await http_node.run_http(nctx, DCFG)
        assert out["status"] == 200
        a1, a2 = _apis(seen)
        c1, c2 = _claims(a1.headers["dpop"]), _claims(a2.headers["dpop"])
        assert "nonce" not in c1 and c2["nonce"] == "n2" and c1["jti"] != c2["jti"]
        assert c2["ath"] == c1["ath"] and a2.headers["authorization"] == f"DPoP {TOKEN}"

    _run(monkeypatch, HOSTS, body)


def test_dpop_api_nonce_retry_only_once(monkeypatch):
    seen = []

    def api(req):
        return httpx.Response(401, json={}, headers={"www-authenticate": 'DPoP error="use_dpop_nonce"',
                                                     "dpop-nonce": "again"})

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen, api=api))
        out = await http_node.run_http(nctx, DCFG)
        assert out["status"] == 401 and len(_apis(seen)) == 2

    _run(monkeypatch, HOSTS, body)


def test_dpop_invalid_token_evicts_cache(monkeypatch):
    seen = []
    answers = {"www": 'DPoP error="invalid_token", error_description="expired"'}

    def api(req):
        return httpx.Response(401, json={"errorCode": "E0000011"}, headers={"WWW-Authenticate": answers["www"]})

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen, api=api))
        out = await http_node.run_http(nctx, DCFG)
        assert out["status"] == 401 and len(_apis(seen)) == 1  # 4xx returned as today; no retry
        assert http_node._TOKENS == {}
        # any other 401 keeps the cached token
        answers["www"] = 'DPoP error="insufficient_scope"'
        out = await http_node.run_http(nctx, DCFG)
        assert out["status"] == 401 and len(_toks(seen)) == 4  # next run minted a fresh token (nonce dance again)
        assert len(http_node._TOKENS) == 1
        await http_node.run_http(nctx, DCFG)
        assert len(_toks(seen)) == 4 and len(_apis(seen)) == 3

    _run(monkeypatch, HOSTS, body)


def test_dpop_redaction(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    def api(req):
        return httpx.Response(200, json={"echo": dict(req.headers), "raw": req.headers["dpop"]},
                              headers={"X-Echo": req.headers["authorization"], "X-Proof": req.headers["dpop"]})

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen, api=api))
        out = await http_node.run_http(nctx, DCFG)
        dumped = json.dumps(out) + str(out)
        proofs = [r.headers["dpop"] for r in seen]
        assertions = [parse_qs(r.content.decode())["client_assertion"][0] for r in _toks(seen)]
        assert len(proofs) == 3 and len(assertions) == 2
        for s in (TOKEN, *proofs, *assertions, '"d":', "PRIVATE KEY"):
            assert s not in dumped and s not in caplog.text

    _run(monkeypatch, HOSTS, body)


def test_dpop_api_error_hides_token_and_proof(monkeypatch):
    seen = []

    def api(req):
        raise httpx.ConnectError(f"boom {req.headers['authorization']} {req.headers['dpop']}")

    async def body(nctx):
        base._install(monkeypatch, _dpop_handler(seen, api=api))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, DCFG)
        msg = str(ei.value)
        assert TOKEN not in msg and _apis(seen)[0].headers["dpop"] not in msg

    _run(monkeypatch, HOSTS, body)


def test_htu_and_proof_units():
    assert http_node._htu("https://API.example.com:8443/a/b?x=1#f") == "https://api.example.com:8443/a/b"
    assert http_node._htu("https://u:p@api.example.com/x") == "https://api.example.com/x"
    assert http_node._htu("https://api.example.com") == "https://api.example.com/"
    key = ec.generate_private_key(ec.SECP256R1())
    p = http_node._dpop_proof(key, "delete", "https://api.example.com/x?y=2", nonce="nn", token="tk")
    hdr = jwt.get_unverified_header(p)
    assert hdr["typ"] == "dpop+jwt"  # PyJWT must not overwrite typ with "JWT"
    nums = key.public_key().public_numbers()
    assert hdr["jwk"] == {"kty": "EC", "crv": "P-256", "x": _b64(nums.x.to_bytes(32, "big")),
                          "y": _b64(nums.y.to_bytes(32, "big"))}
    c = jwt.decode(p, key.public_key(), algorithms=["ES256"])
    assert c["htm"] == "DELETE" and c["htu"] == "https://api.example.com/x" and c["nonce"] == "nn"
    assert c["ath"] == _b64(hashlib.sha256(b"tk").digest())
    assert set(c) == {"htm", "htu", "iat", "jti", "nonce", "ath"}
