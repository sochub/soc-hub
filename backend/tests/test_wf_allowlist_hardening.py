"""Tenant HTTP allowlist hardening: entries are validated, allowlisting never unlocks loopback/link-local/
metadata, private ranges need the platform setting, and secrets only travel over https."""
import asyncio
import secrets as pysecrets
import socket
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete

import app.db.base  # noqa: F401
from app.ai import llm
from app.ai.errors import AIUnavailable
from app.ai.providers import ProviderConfig
from app.api import deps
from app.api.v1 import ai_config as ai_config_api
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.secrets.refs import placeholder
from app.utils.crypto import encrypt
from app.workflows import ssrf
from app.workflows.nodes import NodeContext, NodeError
from app.workflows.nodes import http as http_node

DNS = {
    "api.example.com": ["93.184.216.34"],
    "login.example.com": ["93.184.216.35"],
    "loop.corp.example.com": ["127.0.0.1"],
    "meta.corp.example.com": ["169.254.169.254"],
    "mapped.corp.example.com": ["::ffff:127.0.0.1"],
    "mappedmeta.corp.example.com": ["::ffff:169.254.169.254"],
    "v6loop.corp.example.com": ["::1"],
    "ec2v6.corp.example.com": ["fd00:ec2::254"],
    "linklocal6.corp.example.com": ["fe80::1"],
    "zero.corp.example.com": ["0.0.0.0"],
    "mcast.corp.example.com": ["224.0.0.1"],
    "onprem.corp.example.com": ["10.0.0.5"],
    "cgnat.corp.example.com": ["100.64.1.1"],
    "ula.corp.example.com": ["fd12:3456::1"],
    "mixed.corp.example.com": ["93.184.216.34", "127.0.0.1"],
    "sitelocal.corp.example.com": ["fec0::1"],
}
ALWAYS_BLOCKED = ["loop", "meta", "mapped", "mappedmeta", "v6loop", "ec2v6", "linklocal6", "zero", "mcast"]
PRIVATE = ["onprem", "cgnat", "ula", "sitelocal"]


def fake_resolve(host, port, *a, **kw):
    if host not in DNS:
        raise socket.gaierror("nx")
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in DNS[host]]


@pytest.fixture
def dns(monkeypatch):
    monkeypatch.setattr(ssrf, "_resolve", fake_resolve)


@pytest.fixture
def private_on(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_ALLOW_PRIVATE_ALLOWLIST", True)


@pytest.fixture
def private_off(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_ALLOW_PRIVATE_ALLOWLIST", False)


# --- SSRF guard ----------------------------------------------------------------------------------

def test_private_allowlist_setting_defaults_off():
    assert type(settings).model_fields["WORKFLOW_ALLOW_PRIVATE_ALLOWLIST"].default is False


@pytest.mark.parametrize("name", ALWAYS_BLOCKED + ["mixed"])
def test_allowlisted_host_never_reaches_dangerous_addresses(dns, private_on, name):
    host = f"{name}.corp.example.com"
    with pytest.raises(ssrf.SSRFError):
        ssrf.assert_url_allowed(f"http://{host}/x", [host])
    with pytest.raises(ssrf.SSRFError):
        ssrf.assert_url_allowed(f"https://{host}/x", ["*.corp.example.com"])


@pytest.mark.parametrize("name", PRIVATE)
def test_allowlisted_private_needs_platform_setting(dns, private_off, name):
    host = f"{name}.corp.example.com"
    with pytest.raises(ssrf.SSRFError, match="WORKFLOW_ALLOW_PRIVATE_ALLOWLIST"):
        ssrf.assert_url_allowed(f"https://{host}/x", [host])


@pytest.mark.parametrize("name", PRIVATE)
def test_allowlisted_private_allowed_with_setting_and_pinned(dns, private_on, name):
    host = f"{name}.corp.example.com"
    assert ssrf.assert_url_allowed(f"https://{host}/x", [host]) == DNS[host][0]
    assert ssrf.assert_url_allowed(f"https://{host}/x", ["*.corp.example.com"]) == DNS[host][0]


@pytest.mark.parametrize("name", PRIVATE)
def test_unlisted_private_still_blocked_with_setting(dns, private_on, name):
    with pytest.raises(ssrf.SSRFError):
        ssrf.assert_url_allowed(f"https://{name}.corp.example.com/x", ["other.example.com"])


def test_wildcard_does_not_match_its_own_base(dns, private_on):
    with pytest.raises(ssrf.SSRFError):
        ssrf.assert_url_allowed("https://onprem.corp.example.com/x", ["*.onprem.corp.example.com"])


def test_public_host_passes_and_allowlisted_public_is_pinned(dns, private_off):
    assert ssrf.assert_url_allowed("https://api.example.com/x", []) == "93.184.216.34"
    # allowlisted hosts are no longer "trusted by name": the vetted address is still returned for pinning
    assert ssrf.assert_url_allowed("https://api.example.com/x", ["api.example.com"]) == "93.184.216.34"


def test_legacy_invalid_stored_entries_are_ignored(dns, private_on):
    # entries saved before validation existed (IP literals, single-label names) no longer unlock anything
    for url, entry in (("http://10.0.0.5/x", "10.0.0.5"), ("http://onprem/x", "onprem")):
        with pytest.raises(ssrf.SSRFError):
            ssrf.assert_url_allowed(url, [entry], resolve=lambda h, p, *a, **k: [(2, 1, 6, "", ("10.0.0.5", p))])


# --- allowlist entry validation --------------------------------------------------------------------

BAD_ENTRIES = ["127.0.0.1", "localhost", "redis", "http://x.com", "x.com:8080", "169.254.169.254", "*.",
               "db", "backend", "*.com", "user@x.com", "x.com/path", "[::1]", "::1", "a..b.com", "-a.example.com",
               "a_b.example.com", "foo.localhost", "0x7f.0x1", "x" * 250 + ".com", "*.*.example.com", "*"]


@pytest.mark.parametrize("entry", BAD_ENTRIES)
def test_bad_entries_rejected(entry):
    with pytest.raises(ssrf.AllowlistError) as e:
        ssrf.normalize_allowlist([entry])
    assert e.value.entry == entry


def test_good_entries_normalized():
    assert ssrf.normalize_allowlist(["api.example.com", "*.corp.example.com", " API.Example.COM. ", ""]) == [
        "*.corp.example.com", "api.example.com"]


@pytest.mark.parametrize("raw,norm", [
    ("https://api.cloudflare.com", "api.cloudflare.com"),
    ("https://user:pw@API.example.com:8443/path?q=1", "api.example.com"),
    ("api.example.com:443", "api.example.com"),
    ("*.corp.example.com", "*.corp.example.com"),
    ("http://127.0.0.1:8080/", None),
    ("intranet", None),
])
def test_stored_legacy_entries_normalized(raw, norm):
    assert ssrf.stored_entry(raw) == norm


def test_stored_legacy_url_entry_matches_as_hostname(dns, private_off):
    assert ssrf.host_on_allowlist("api.cloudflare.com", ["https://api.cloudflare.com"])
    assert ssrf.stored_allowlist(["https://b.example.com", "a.example.com:443", "redis"], 7) == [
        "a.example.com", "b.example.com"]


def test_dropped_entries_logged_redacted(caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    out = ssrf.stored_allowlist(["https://user:secret@x.example.com/p?q=1",
                                 "https://user:secret@intranet/p?q=1",
                                 "user:secret@10.0.0.5:8080/p?q=1",
                                 "weird secret/p?q=1",
                                 "https://user:pa/ss@host.example.com",
                                 "user:pw/x@h.com"], 9)
    assert out == ["x.example.com"]
    text = caplog.text + " ".join(str(r.args) for r in caplog.records)
    assert caplog.records, "dropped entries are logged"
    for bad in ("secret", "/p", "q=1", "user"):
        assert bad not in text, bad


@pytest.mark.parametrize("stored", ["api.example.com", {"hosts": ["api.example.com"]}, 5])
def test_stored_non_list_treated_as_empty(caplog, stored):
    import logging
    caplog.set_level(logging.WARNING)
    assert ssrf.stored_allowlist(stored, 11) == []
    assert ssrf.stored_allowlist(None, 11) == []
    assert "tenant=11" in caplog.text and "api.example.com" not in caplog.text
    assert not ssrf.host_on_allowlist("api.example.com", stored)


def test_too_many_entries():
    with pytest.raises(ssrf.AllowlistError):
        ssrf.normalize_allowlist([f"h{i}.example.com" for i in range(ssrf.MAX_ALLOWLIST + 1)])


def test_allowlist_put_api():
    async def scenario():
        await engine.dispose()
        h = pysecrets.token_hex(3)
        ids = {}
        try:
            async with AsyncSessionLocal() as db:
                t = Tenant(name="wf-test", slug=f"wf-test-{h}-al")
                u = User(email=f"wf-al-{h}@example.test", hashed_password=get_password_hash(pysecrets.token_hex(8)),
                         is_active=True)
                db.add_all([t, u])
                await db.commit()
                ids.update(t=t.id, u=u.id)
            app.dependency_overrides[deps.get_current_active_user] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ids["t"]
            app.dependency_overrides[deps.require_admin] = lambda: u
            url = "/api/v1/workflows/settings/http-allowlist"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                for bad in ["127.0.0.1", "localhost", "redis", "http://x.com", "x.com:8080", "169.254.169.254", "*."]:
                    r = await c.put(url, json={"hosts": ["api.example.com", bad]})
                    assert r.status_code == 422 and repr(bad) in r.json()["detail"], (bad, r.status_code, r.text)
                r = await c.get(url)
                assert r.json() == {"hosts": []}, "rejected PUT must not save anything"
                r = await c.put(url, json={"hosts": [f"h{i}.example.com" for i in range(101)]})
                assert r.status_code == 422, r.text
                r = await c.put(url, json={"hosts": ["api.example.com", "*.corp.example.com"]})
                assert r.status_code == 200 and r.json() == {"hosts": ["*.corp.example.com", "api.example.com"]}, r.text
                # legacy values stored before validation: GET normalizes, and saving the GET result back works
                async with AsyncSessionLocal() as db:
                    t = await db.get(Tenant, ids["t"])
                    t.workflow_http_allowlist = ["https://api.cloudflare.com", "x.example.com:8443", "127.0.0.1"]
                    await db.commit()
                r = await c.get(url)
                assert r.json() == {"hosts": ["api.cloudflare.com", "x.example.com"]}, r.text
                r = await c.put(url, json=r.json())
                assert r.status_code == 200 and r.json() == {"hosts": ["api.cloudflare.com", "x.example.com"]}, r.text
        finally:
            app.dependency_overrides.clear()
            async with AsyncSessionLocal() as db:
                if ids.get("t"):
                    await db.execute(delete(AuditLog).where(AuditLog.tenant_id == ids["t"]))
                    await db.execute(delete(Tenant).where(Tenant.id == ids["t"]))
                if ids.get("u"):
                    await db.execute(delete(User).where(User.id == ids["u"]))
                await db.commit()
            await engine.dispose()

    asyncio.run(scenario())


# --- http_request node -----------------------------------------------------------------------------

N = "0123456789abcdef"
TOK = "tok-SECRET-abc123"
_REAL = httpx.AsyncClient


async def _with_tenant(allowlist, body):
    await engine.dispose()
    ids = {}
    try:
        async with AsyncSessionLocal() as db:
            t = Tenant(name="wf-test", slug=f"wf-test-{pysecrets.token_hex(3)}-hd", workflow_http_allowlist=allowlist)
            db.add(t)
            await db.flush()
            ids["t"] = t.id
            s = TenantSecret(tenant_id=t.id, name="TOK", value_enc=encrypt(TOK),
                             allowed_hosts=["api.example.com", "login.example.com", "*.corp.example.com"])
            db.add(s)
            await db.flush()
            ids["s"] = s.id
            await db.commit()
            nctx = NodeContext(db=db, run=SimpleNamespace(tenant_id=t.id), step=None, node={}, ctx={},
                               secret_nonce=N, secret_names={"TOK"})
            await body(nctx)
    finally:
        async with AsyncSessionLocal() as db:
            if ids.get("s"):
                await db.execute(delete(TenantSecret).where(TenantSecret.id == ids["s"]))
            if ids.get("t"):
                await db.execute(delete(Tenant).where(Tenant.id == ids["t"]))
            await db.commit()
        await engine.dispose()


def _transport(monkeypatch, seen):
    def handler(req):
        seen.append(req)
        if req.headers.get("host", "").startswith("login."):
            return httpx.Response(200, json={"access_token": "minted", "expires_in": 60})
        return httpx.Response(200, json={"ok": True})
    monkeypatch.setattr(http_node.httpx, "AsyncClient", lambda **kw: _REAL(transport=httpx.MockTransport(handler), **kw))
    http_node._TOKENS.clear()


@pytest.mark.parametrize("where", ["header", "query", "body"])
def test_http_with_secret_refused(monkeypatch, dns, where):
    seen = []
    p = placeholder("TOK", N)
    cfg = {"method": "POST", "url": "http://api.example.com/x"}
    if where == "header":
        cfg["headers"] = {"Authorization": f"Bearer {p}"}
    elif where == "query":
        cfg["url"] += f"?key={p}"
    else:
        cfg["body"] = {"token": p}

    async def body(nctx):
        _transport(monkeypatch, seen)
        with pytest.raises(NodeError, match="secrets are only sent over https"):
            await http_node.run_http(nctx, cfg)
        assert seen == []

    asyncio.run(_with_tenant(["api.example.com"], body))  # allowlisting no longer permits plain http


def test_https_with_secret_allowed(monkeypatch, dns):
    seen = []

    async def body(nctx):
        _transport(monkeypatch, seen)
        out = await http_node.run_http(nctx, {"method": "GET", "url": "https://api.example.com/x",
                                              "headers": {"Authorization": f"Bearer {placeholder('TOK', N)}"}})
        assert out["status"] == 200 and len(seen) == 1
        assert seen[0].headers["authorization"] == f"Bearer {TOK}"
        assert seen[0].url.host == "93.184.216.34"  # pinned

    asyncio.run(_with_tenant([], body))


def test_http_without_secret_unchanged(monkeypatch, dns):
    seen = []

    async def body(nctx):
        _transport(monkeypatch, seen)
        out = await http_node.run_http(nctx, {"method": "GET", "url": "http://api.example.com/x"})
        assert out["status"] == 200 and len(seen) == 1 and seen[0].url.host == "93.184.216.34"

    asyncio.run(_with_tenant([], body))


def test_http_basic_auth_refused_over_http(monkeypatch, dns):
    seen = []

    async def body(nctx):
        _transport(monkeypatch, seen)
        with pytest.raises(NodeError, match="secrets are only sent over https"):
            await http_node.run_http(nctx, {"method": "GET", "url": "http://api.example.com/x",
                                            "auth": {"type": "basic", "username": "u",
                                                     "password": placeholder("TOK", N)}})
        assert seen == []

    asyncio.run(_with_tenant([], body))


@pytest.mark.parametrize("name", ["loop", "meta", "mapped"])
def test_node_allowlisted_dangerous_host_blocked(monkeypatch, dns, private_on, name):
    seen = []
    host = f"{name}.corp.example.com"

    async def body(nctx):
        _transport(monkeypatch, seen)
        with pytest.raises(NodeError):
            await http_node.run_http(nctx, {"method": "GET", "url": f"http://{host}/x"})
        assert seen == []

    asyncio.run(_with_tenant([host], body))


def test_node_allowlisted_private_host_needs_setting(monkeypatch, dns, private_off):
    seen = []

    async def body(nctx):
        _transport(monkeypatch, seen)
        with pytest.raises(NodeError, match="WORKFLOW_ALLOW_PRIVATE_ALLOWLIST"):
            await http_node.run_http(nctx, {"method": "GET", "url": "http://onprem.corp.example.com/x"})
        assert seen == []
        monkeypatch.setattr(settings, "WORKFLOW_ALLOW_PRIVATE_ALLOWLIST", True)
        out = await http_node.run_http(nctx, {"method": "GET", "url": "http://onprem.corp.example.com/x"})
        assert out["status"] == 200 and seen[0].url.host == "10.0.0.5"  # pinned even though allowlisted

    asyncio.run(_with_tenant(["onprem.corp.example.com"], body))


OAUTH = {"type": "oauth2_client_credentials", "client_id": "cid", "client_secret": placeholder("TOK", N)}


def test_oauth_token_url_http_refused(monkeypatch, dns):
    seen = []

    async def body(nctx):
        _transport(monkeypatch, seen)
        with pytest.raises(NodeError, match="secrets are only sent over https"):
            await http_node.run_http(nctx, {"method": "GET", "url": "https://api.example.com/x",
                                            "auth": {**OAUTH, "token_url": "http://login.example.com/token"}})
        assert seen == []

    asyncio.run(_with_tenant(["login.example.com"], body))


def test_oauth_token_url_uses_hardened_guard(monkeypatch, dns, private_on):
    seen = []

    async def body(nctx):
        _transport(monkeypatch, seen)
        with pytest.raises(NodeError, match="token URL"):
            await http_node.run_http(nctx, {"method": "GET", "url": "https://api.example.com/x",
                                            "auth": {**OAUTH, "token_url": "https://meta.corp.example.com/token"}})
        assert seen == []

    asyncio.run(_with_tenant(["meta.corp.example.com"], body))


# --- tenant AI api_base ----------------------------------------------------------------------------

@pytest.fixture
def ai_calls(monkeypatch):
    seen = []

    async def fake(**kw):
        seen.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))])
    monkeypatch.setattr(llm.litellm, "acompletion", fake)
    return seen


def test_ai_api_base_allowlisted_loopback_blocked(dns, private_on, ai_calls):
    cfg = ProviderConfig("ollama", "llama3", "tenant", tenant_id=1, api_base="http://loop.corp.example.com:11434")
    with pytest.raises(AIUnavailable, match="not allowed"):
        asyncio.run(llm.complete_with(cfg, [{"role": "user", "content": "x"}], allowlist=["loop.corp.example.com"]))
    assert ai_calls == []


def test_ai_api_base_http_with_key_refused(dns, ai_calls):
    cfg = ProviderConfig("openai_compatible", "m", "tenant", tenant_id=1, api_base="http://api.example.com/v1",
                         secrets={"api_key": "sk-abcdef123456"})
    with pytest.raises(AIUnavailable, match="secrets are only sent over https"):
        asyncio.run(llm.complete_with(cfg, [{"role": "user", "content": "x"}]))
    assert ai_calls == []
    cfg.api_base = "https://api.example.com/v1"
    assert asyncio.run(llm.complete_with(cfg, [{"role": "user", "content": "x"}])) == "hi"


def test_ai_config_validate_uses_same_guard(monkeypatch, dns, private_on):
    async def allow(db, tid):
        return ["meta.corp.example.com"]
    monkeypatch.setattr(ai_config_api, "tenant_allowlist", allow)
    cfg = ProviderConfig("ollama", "llama3", "tenant", tenant_id=1, api_base="http://meta.corp.example.com:11434")
    with pytest.raises(HTTPException) as e:
        asyncio.run(ai_config_api._validate(None, cfg, 1, allow_incomplete_role=False))
    assert e.value.status_code == 400
    keyed = ProviderConfig("openai_compatible", "m", "tenant", tenant_id=1, api_base="http://api.example.com/v1",
                           secrets={"api_key": "sk-abcdef123456"})
    with pytest.raises(HTTPException, match="https") as e:
        asyncio.run(ai_config_api._validate(None, keyed, 1, allow_incomplete_role=False))
    assert e.value.status_code == 400
