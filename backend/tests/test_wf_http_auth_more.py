"""http_request auth=basic / aws_sigv4: credentials resolved from host-bound secrets, derived values redacted."""
import asyncio
import base64
import datetime
import hashlib
import hmac
import json
import logging
import secrets as pysecrets
from types import SimpleNamespace

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
from app.workflows.validation import validate_graph

N = "0123456789abcdef"
VALUES = {"AKID": "AKIAEXAMPLE123", "SK": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "ST": "sess-TOKEN-abc",
          "USER": "svc-user", "PW": "pw-SECRET-789"}
HOST = "guardduty.us-east-1.amazonaws.com"
_REAL = httpx.AsyncClient


def _install(monkeypatch, handler, pinned_ip=None):
    monkeypatch.setattr(http_node.httpx, "AsyncClient", lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(http_node, "assert_url_allowed", lambda url, allow: pinned_ip)
    monkeypatch.setattr("botocore.auth.get_current_datetime",
                        lambda: datetime.datetime(2026, 10, 19, 12, 0, 0, tzinfo=datetime.timezone.utc))


async def _with_secrets(allowed_hosts, body):
    await engine.dispose()
    ids = {"t": None, "s": []}
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{pysecrets.token_hex(3)}-am")
            db.add(t)
            await db.flush()
            ids["t"] = t.id
            for name, v in VALUES.items():
                s = TenantSecret(tenant_id=t.id, name=name, value_enc=encrypt(v), allowed_hosts=allowed_hosts)
                db.add(s)
                await db.flush()
                ids["s"].append(s.id)
            await db.commit()
            nctx = NodeContext(db=db, run=SimpleNamespace(tenant_id=t.id), step=None, node={}, ctx={},
                               secret_nonce=N, secret_names=set(VALUES))
            await body(nctx)
    finally:
        async with AsyncSessionLocal() as db:
            if ids["s"]:
                await db.execute(delete(TenantSecret).where(TenantSecret.id.in_(ids["s"])))
            if ids["t"]:
                await db.execute(delete(Tenant).where(Tenant.id == ids["t"]))
            await db.commit()
        await engine.dispose()


SIGV4 = {"method": "POST", "url": f"https://{HOST}/detector/abc/findings",
         "headers": {"Authorization": "Bearer stale"}, "body": {"maxResults": 50, "sortCriteria": {"attributeName": "updatedAt"}},
         "auth": {"type": "aws_sigv4", "access_key_id": placeholder("AKID", N), "secret_access_key": placeholder("SK", N),
                  "session_token": placeholder("ST", N), "region": "us-east-1", "service": "guardduty"}}
BASIC = {"method": "GET", "url": "https://tap-api-v2.proofpoint.com/v2/siem/all",
         "auth": {"type": "basic", "username": placeholder("USER", N), "password": placeholder("PW", N)}}


def _expected_sig(req: httpx.Request) -> tuple[str, str]:
    """Independent SigV4 (AWS docs algorithm) over what was actually sent."""
    signed = ["content-type", "host", "x-amz-date", "x-amz-security-token"]
    amz_date = req.headers["x-amz-date"]
    canonical = "\n".join([req.method, req.url.raw_path.decode(), "",
                           *[f"{h}:{req.headers[h].strip()}" for h in signed], "",
                           ";".join(signed), hashlib.sha256(req.content).hexdigest()])
    scope = f"{amz_date[:8]}/us-east-1/guardduty/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = ("AWS4" + VALUES["SK"]).encode()
    for part in (amz_date[:8], "us-east-1", "guardduty", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    return hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest(), ";".join(signed)


def test_sigv4_signature_and_redaction(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"echo": dict(req.headers)}, headers={"X-Echo": req.headers["authorization"]})

    async def body(nctx):
        _install(monkeypatch, handler, pinned_ip="203.0.113.7")
        out = await http_node.run_http(nctx, SIGV4)
        (req,) = seen
        assert req.url.host == "203.0.113.7" and req.headers["host"] == HOST  # pinned, but signed/sent Host = URL host
        assert req.headers["x-amz-date"] == "20261019T120000Z"
        assert req.headers["x-amz-security-token"] == VALUES["ST"]
        assert json.loads(req.content) == SIGV4["body"]
        sig, signed = _expected_sig(req)
        assert req.headers["authorization"] == (
            f"AWS4-HMAC-SHA256 Credential={VALUES['AKID']}/20261019/us-east-1/guardduty/aws4_request, "
            f"SignedHeaders={signed}, Signature={sig}")
        dumped = json.dumps(out) + str(out)
        for v in (sig, VALUES["SK"], VALUES["ST"], VALUES["AKID"]):
            assert v not in dumped and v not in caplog.text

    asyncio.run(_with_secrets([HOST], body))


def test_sigv4_error_text_has_no_signature(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        raise httpx.ConnectError(f"boom {req.headers['authorization']} {VALUES['SK']}")

    async def body(nctx):
        _install(monkeypatch, handler)
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, SIGV4)
        sig = seen[0].headers["authorization"].rsplit("Signature=", 1)[1]
        assert sig not in str(ei.value) and VALUES["SK"] not in str(ei.value)

    asyncio.run(_with_secrets([HOST], body))


def test_sigv4_secret_not_bound_to_host(monkeypatch):
    seen = []

    async def body(nctx):
        _install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200))
        with pytest.raises(NodeError) as ei:
            await http_node.run_http(nctx, SIGV4)
        assert f"not allowed for host {HOST}" in str(ei.value)
        assert seen == []

    asyncio.run(_with_secrets(["other.example.com"], body))


def test_basic_header_and_redaction(monkeypatch):
    seen = []
    b64 = base64.b64encode(f"{VALUES['USER']}:{VALUES['PW']}".encode()).decode()

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"echo": req.headers["authorization"]})

    async def body(nctx):
        _install(monkeypatch, handler)
        out = await http_node.run_http(nctx, BASIC)
        assert seen[0].headers["authorization"] == f"Basic {b64}"
        dumped = json.dumps(out)
        assert b64 not in dumped and VALUES["PW"] not in dumped

    asyncio.run(_with_secrets(["tap-api-v2.proofpoint.com"], body))


def test_basic_literal_password_refused(monkeypatch):
    seen = []
    cfg = {**BASIC, "auth": {**BASIC["auth"], "password": "hunter2"}}

    async def body(nctx):
        _install(monkeypatch, lambda req: seen.append(req) or httpx.Response(200))
        with pytest.raises(NodeError, match="auth.password must be a secret reference"):
            await http_node.run_http(nctx, cfg)
        assert seen == []

    asyncio.run(_with_secrets(["tap-api-v2.proofpoint.com"], body))


def _g(auth):
    return {"nodes": [{"id": "start", "type": "trigger", "config": {}},
                      {"id": "h", "type": "http_request", "config": {"method": "GET", "url": "https://x.example.com/",
                                                                    "auth": auth}}],
            "edges": [{"source": "start", "target": "h"}]}


def _msgs(auth):
    return [e["message"] for e in validate_graph(_g(auth), "manual")]


def test_validation():
    ok_basic = {"type": "basic", "username": "u", "password": "{{ secrets.PW }}"}
    ok_aws = {"type": "aws_sigv4", "access_key_id": "{{ secrets.AKID }}", "secret_access_key": "{{ secrets.SKEY }}",
              "region": "us-east-1", "service": "guardduty"}
    assert not [m for m in _msgs(ok_basic) if "auth" in m]
    assert not [m for m in _msgs(ok_aws) if "auth" in m]
    assert "auth.password must be a secret reference like {{ secrets.NAME }}" in _msgs({**ok_basic, "password": "x"})
    assert ("auth.secret_access_key must be a secret reference like {{ secrets.NAME }}"
            in _msgs({**ok_aws, "secret_access_key": "x"}))
    assert "auth.region must match [a-z0-9-]{2,32}" in _msgs({**ok_aws, "region": "US East"})
    assert "missing required field 'auth.service'" in _msgs({k: v for k, v in ok_aws.items() if k != "service"})
    assert "auth.type must be oauth2_client_credentials, oauth2_private_key_jwt, basic, aws_sigv4 or google_service_account" in _msgs({"type": "digest"})
