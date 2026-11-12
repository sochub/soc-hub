import json
import secrets
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.enrichment.config import decrypt_keys, runnable_sources, load_settings
from app.enrichment.sources import REGISTRY, misp
from app.main import app
from app.models.tenant import Tenant
from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.models.user import User

@pytest.fixture(autouse=True)
def _allow_hosts(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MISP_PRIVATE_HOSTS", ["misp.example", "m2.example"])
    _fake_dns(monkeypatch)


def _fake_dns(monkeypatch, name_ip="10.9.9.9"):
    """Stub DNS inside hostguard only: IP literals resolve to themselves, names to `name_ip`."""
    import ipaddress
    from types import SimpleNamespace
    from app.utils import hostguard

    def fake(h, *a, **k):
        try:
            ip = str(ipaddress.ip_address(h))
        except ValueError:
            ip = name_ip
        return [(2, 1, 6, "", (ip, 0))]
    monkeypatch.setattr(hostguard, "socket", SimpleNamespace(getaddrinfo=fake))


CFG = json.dumps({"url": "https://misp.example/", "key": "MISP-SECRET-KEY", "verify": True})


def _mock(handler):
    return httpx.MockTransport(handler)


def _resp(attrs, status=200):
    return httpx.Response(status, json={"response": {"Attribute": attrs}})


def test_registered():
    assert REGISTRY["misp"] is misp and misp.TYPES == frozenset({"ip", "domain", "url", "file_hash"})


@pytest.mark.asyncio
async def test_hit_with_to_ids():
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["host"] = str(req.url), req.headers["authorization"], req.headers["host"]
        seen["sni"] = req.extensions.get("sni_hostname")
        seen["body"] = json.loads(req.content)
        return _resp([
            {"event_id": "7", "to_ids": True, "Tag": [{"name": "misp-galaxy:x"}],
             "Event": {"id": "7", "info": "Campaign X", "Tag": [{"name": "tlp:amber"}]}},
            {"event_id": "7", "to_ids": False, "Event": {"id": "7", "info": "Campaign X"}},
            {"event_id": "9", "to_ids": False, "Event": {"id": "9", "info": "Other"},
             "EventTag": [{"Tag": {"name": "tlp:green"}}]},
        ])

    r = await misp.lookup("ip", "1.2.3.4", CFG, transport=_mock(handler))
    # N4: connects to the vetted IP; Host and SNI keep the configured name
    assert seen["url"] == "https://10.9.9.9/attributes/restSearch"
    assert seen["host"] == "misp.example" and seen["sni"] == "misp.example"
    assert seen["auth"] == "MISP-SECRET-KEY"
    assert seen["body"] == {"value": "1.2.3.4", "limit": 50, "includeContext": True, "includeEventTags": True}
    assert r.status == "ok" and r.verdict == "suspicious" and r.score == "3 attributes"
    assert r.link == "https://misp.example/events/view/7"
    assert r.summary["events"] == [{"id": "7", "info": "Campaign X"}, {"id": "9", "info": "Other"}]
    assert r.summary["tags"][:2] == ["tlp:amber", "tlp:green"] and "misp-galaxy:x" in r.summary["tags"]


@pytest.mark.asyncio
async def test_hit_without_to_ids_is_unknown():
    r = await misp.lookup("domain", "a.example", CFG, transport=_mock(
        lambda req: _resp([{"event_id": "1", "to_ids": False}])))
    assert r.status == "ok" and r.verdict == "unknown"


@pytest.mark.asyncio
async def test_empty_is_not_found():
    r = await misp.lookup("ip", "1.2.3.4", CFG, transport=_mock(lambda req: _resp([])))
    assert r.status == "not_found"
    r = await misp.lookup("ip", "1.2.3.4", CFG, transport=_mock(lambda req: httpx.Response(200, json={"response": []})))
    assert r.status == "not_found"


@pytest.mark.asyncio
async def test_403_invalid_key():
    r = await misp.lookup("ip", "1.2.3.4", CFG, transport=_mock(lambda req: httpx.Response(403)))
    assert r.status == "error" and r.error == "invalid API key"


@pytest.mark.asyncio
async def test_429_rate_limited():
    r = await misp.lookup("ip", "1.2.3.4", CFG, transport=_mock(lambda req: httpx.Response(429)))
    assert r.status == "rate_limited"


@pytest.mark.asyncio
async def test_malformed_json_and_bad_config():
    r = await misp.lookup("ip", "1.2.3.4", CFG, transport=_mock(lambda req: httpx.Response(200, content=b"<html>")))
    assert r.status == "error" and "unexpected response" in r.error
    r = await misp.lookup("ip", "1.2.3.4", "not json")
    assert r.status == "error" and "MISP" in r.error
    r = await misp.lookup("ip", "1.2.3.4", json.dumps({"url": "https://x"}))
    assert r.status == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("verify", [False, True])
async def test_verify_flag_reaches_client(verify):
    cfg = json.dumps({"url": "https://misp.example", "key": "k", "verify": verify})
    real = httpx.AsyncClient
    kw = {}

    def factory(*a, **k):
        kw.update(k)
        return real(*a, **{**k, "transport": _mock(lambda req: _resp([]))})

    with patch("app.enrichment.sources.misp.httpx.AsyncClient", factory):
        await misp.lookup("ip", "1.2.3.4", cfg)
    assert kw["verify"] is verify and kw["follow_redirects"] is False


def test_runnable_requires_complete_triple():
    from app.enrichment.config import EnrichmentSettings
    s = EnrichmentSettings(keys={"misp": CFG})
    assert "misp" in runnable_sources(s)
    assert "misp" not in runnable_sources(EnrichmentSettings(keys={"misp": json.dumps({"url": "https://x", "key": ""})}))
    assert "misp" not in runnable_sources(EnrichmentSettings())


@pytest.mark.asyncio
async def test_config_put_assembles_and_merges():
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {}
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-misp")
            u = User(email=f"misp-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([t, u])
            await db.commit()
            ids["t"], ids["u"] = int(t.id), int(u.id)
            from httpx import ASGITransport, AsyncClient

            async def _db():
                async with AsyncSessionLocal() as s:
                    yield s

            app.dependency_overrides[deps.get_db] = _db
            app.dependency_overrides[deps.require_admin] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ids["t"]
            try:
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    async def stored():
                        async with AsyncSessionLocal() as s:
                            row = (await s.execute(select(TenantEnrichmentConfig).where(
                                TenantEnrichmentConfig.tenant_id == ids["t"]))).scalars().first()
                            return json.loads(decrypt_keys(row.credentials_enc, ids["t"])[0]["misp"])

                    base = {"misp_url": "https://misp.example/", "misp_key": "K1"}
                    r = await c.put("/api/v1/enrichment/config", json=base)
                    assert r.status_code == 200, r.text
                    assert r.json()["credentials_set"]["misp"] is True
                    assert r.json()["misp_verify_tls"] is True and r.json()["misp_url"] == "https://misp.example"
                    assert "K1" not in r.text
                    assert await stored() == {"url": "https://misp.example", "key": "K1", "verify": True}
                    # verify only: merges, keeps key
                    r = await c.put("/api/v1/enrichment/config", json={"misp_verify_tls": False})
                    assert r.json()["misp_verify_tls"] is False
                    assert await stored() == {"url": "https://misp.example", "key": "K1", "verify": False}
                    # url on the same host and port: keeps key
                    await c.put("/api/v1/enrichment/config", json={"misp_url": "https://misp.example:443/"})
                    assert await stored() == {"url": "https://misp.example:443", "key": "K1", "verify": False}
                    # N4: url to another host without a new key is refused and the stored key stays put
                    r = await c.put("/api/v1/enrichment/config", json={"misp_url": "https://m2.example"})
                    assert r.status_code == 422 and "key is required" in r.text
                    r = await c.put("/api/v1/enrichment/config", json={"misp_url": "https://misp.example:8443"})
                    assert r.status_code == 422
                    assert (await stored())["key"] == "K1"
                    r = await c.put("/api/v1/enrichment/config", json={"misp_url": "https://m2.example", "misp_key": "K1b"})
                    assert r.status_code == 200, r.text
                    assert await stored() == {"url": "https://m2.example", "key": "K1b", "verify": False}
                    # a key never goes over plain http
                    r = await c.put("/api/v1/enrichment/config", json={"misp_url": "http://m2.example", "misp_key": "K1c"})
                    assert r.status_code == 422 and "https" in r.text
                    # new key
                    await c.put("/api/v1/enrichment/config", json={"misp_key": "K2"})
                    assert (await stored())["key"] == "K2"
                    # bad url rejected
                    r = await c.put("/api/v1/enrichment/config", json={"misp_url": "ftp://x"})
                    assert r.status_code == 422
                    # SSRF guard / userinfo rejected at PUT
                    r = await c.put("/api/v1/enrichment/config", json={"misp_url": "https://127.0.0.1:9", "misp_key": "K3"})
                    assert r.status_code == 422 and "MISP_PRIVATE_HOSTS" in r.text
                    r = await c.put("/api/v1/enrichment/config", json={"misp_url": "https://u:p@m2.example", "misp_key": "K3"})
                    assert r.status_code == 422
                    # clear
                    r = await c.put("/api/v1/enrichment/config", json={"clear_misp": True})
                    assert r.json()["credentials_set"]["misp"] is False
            finally:
                app.dependency_overrides.clear()
        finally:
            async with AsyncSessionLocal() as s:
                if "t" in ids:
                    await s.execute(delete(TenantEnrichmentConfig).where(TenantEnrichmentConfig.tenant_id == ids["t"]))
                    from app.models.audit_log import AuditLog
                    await s.execute(delete(AuditLog).where(AuditLog.tenant_id == ids["t"]))
                    await s.execute(delete(Tenant).where(Tenant.id == ids["t"]))
                if "u" in ids:
                    await s.execute(delete(User).where(User.id == ids["u"]))
                await s.commit()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["https://user:pw@misp.example", "https://mi sp.example", "ftp://misp.example"])
async def test_lookup_rejects_bad_urls(url):
    hit = []
    r = await misp.lookup("ip", "1.2.3.4", json.dumps({"url": url, "key": "k"}),
                          transport=_mock(lambda req: hit.append(1) or _resp([])))
    assert r.status == "error" and not hit


@pytest.mark.asyncio
async def test_lookup_blocks_private_host(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MISP_PRIVATE_HOSTS", [])
    hit = []
    r = await misp.lookup("ip", "1.2.3.4", json.dumps({"url": "https://127.0.0.1:8080", "key": "k"}),
                          transport=_mock(lambda req: hit.append(1) or _resp([])))
    assert r.status == "error" and r.error == "host not allowed" and not hit


def test_check_url_allowlist(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "MISP_PRIVATE_HOSTS", [])
    assert "MISP_PRIVATE_HOSTS" in misp.check_url("http://localhost:8443")
    monkeypatch.setattr(settings, "MISP_PRIVATE_HOSTS", ["localhost"])
    assert misp.check_url("http://localhost:8443") is None


@pytest.mark.asyncio
async def test_clear_misp_with_undecryptable_credentials():
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {}
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="wf-test", slug=f"wf-test-{h}-mispk")
            u = User(email=f"mispk-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
            db.add_all([t, u])
            await db.flush()
            ids["t"], ids["u"] = int(t.id), int(u.id)
            db.add(TenantEnrichmentConfig(tenant_id=ids["t"], credentials_enc="not-decryptable"))
            await db.commit()
            from httpx import ASGITransport, AsyncClient

            async def _db():
                async with AsyncSessionLocal() as s:
                    yield s

            app.dependency_overrides[deps.get_db] = _db
            app.dependency_overrides[deps.require_admin] = lambda: u
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: ids["t"]
            try:
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    r = await c.put("/api/v1/enrichment/config", json={"clear_misp": True})
                    assert r.status_code == 200, r.text
                async with AsyncSessionLocal() as s:
                    row = (await s.execute(select(TenantEnrichmentConfig).where(
                        TenantEnrichmentConfig.tenant_id == ids["t"]))).scalars().first()
                    assert row.credentials_enc is None
            finally:
                app.dependency_overrides.clear()
        finally:
            async with AsyncSessionLocal() as s:
                if "t" in ids:
                    from app.models.audit_log import AuditLog
                    await s.execute(delete(TenantEnrichmentConfig).where(TenantEnrichmentConfig.tenant_id == ids["t"]))
                    await s.execute(delete(AuditLog).where(AuditLog.tenant_id == ids["t"]))
                    await s.execute(delete(Tenant).where(Tenant.id == ids["t"]))
                if "u" in ids:
                    await s.execute(delete(User).where(User.id == ids["u"]))
                await s.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_lookup_pins_vetted_ip_and_requires_https(monkeypatch):
    """N4: the request goes to the address the guard vetted (no second DNS lookup), and never over http."""
    calls = []

    def handler(req):
        calls.append((req.url.host, req.headers["host"]))
        return _resp([])
    r = await misp.lookup("ip", "1.2.3.4", json.dumps({"url": "http://misp.example", "key": "k"}), transport=_mock(handler))
    assert r.status == "error" and "https" in r.error and not calls
    _fake_dns(monkeypatch, "93.184.216.34")
    await misp.lookup("ip", "1.2.3.4", json.dumps({"url": "https://misp.example:8443", "key": "k"}), transport=_mock(handler))
    assert calls == [("93.184.216.34", "misp.example:8443")]
