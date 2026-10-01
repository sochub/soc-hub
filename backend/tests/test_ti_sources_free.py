from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
import pytest

from app.enrichment.sources import REGISTRY, crtsh, rdap

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def T(h):
    return httpx.MockTransport(h)


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    # assert_url_allowed resolves DNS; tests must not touch the network.
    import app.enrichment.sources.rdap as m

    def fake(url, allowlist):
        if urlsplit(url).hostname == "internal.example":
            from app.workflows.ssrf import SSRFError
            raise SSRFError("blocked")
        return "203.0.113.10"
    monkeypatch.setattr(m, "assert_url_allowed", fake)


@pytest.mark.asyncio
async def test_rdap_domain_follows_https_redirect_and_flags_young():
    def h(req):
        if req.url.host == "rdap.org":
            return httpx.Response(302, headers={"location": "https://rdap.verisign.com/com/v1/domain/new.com"})
        return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "2026-09-20T00:00:00Z"}],
                                         "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Reg Inc"]]]}]})
    r = await rdap.lookup("domain", "new.com", transport=T(h), now=NOW)
    assert r.status == "ok" and r.verdict == "suspicious"
    assert r.summary == {"registrar": "Reg Inc", "created": "2026-09-20T00:00:00Z"}


@pytest.mark.asyncio
async def test_rdap_old_domain_unknown_and_404():
    old = T(lambda req: httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "1997-09-15T04:00:00Z"}]}))
    assert (await rdap.lookup("domain", "google.com", transport=old, now=NOW)).verdict == "unknown"
    assert (await rdap.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(404)), now=NOW)).status == "not_found"


@pytest.mark.asyncio
async def test_rdap_ip_summary():
    body = {"name": "GOOGLE", "country": "US", "handle": "NET-8-8-8-0-1", "startAddress": "8.8.8.0", "endAddress": "8.8.8.255"}
    r = await rdap.lookup("ip", "8.8.8.8", transport=T(lambda req: httpx.Response(200, json=body)), now=NOW)
    assert r.verdict == "unknown" and r.summary == {"name": "GOOGLE", "country": "US", "handle": "NET-8-8-8-0-1",
                                                    "range": "8.8.8.0 - 8.8.8.255"}


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["http://rdap.example/domain/x.com", "https://internal.example/domain/x.com"])
async def test_rdap_refuses_unsafe_redirect(location):
    r = await rdap.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(302, headers={"location": location})), now=NOW)
    assert r.status == "error" and r.error == "rdap redirect refused"


@pytest.mark.asyncio
async def test_rdap_relative_location_followed_and_vetted():
    seen = []

    def h(req):
        seen.append(str(req.url))
        if req.url.path.startswith("/domain/") and req.url.host == "rdap.org":
            return httpx.Response(302, headers={"location": "/rdap/x.com"})
        return httpx.Response(200, json={"events": []})
    r = await rdap.lookup("domain", "x.com", transport=T(h), now=NOW)
    assert r.status == "ok" and seen[-1] == "https://rdap.org/rdap/x.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{"location": "//internal.example/domain/x.com"}, {"location": ""}, {}])
async def test_rdap_refuses_protocol_relative_and_missing_location(headers):
    r = await rdap.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(302, headers=headers)), now=NOW)
    assert r.status == "error" and r.error == "rdap redirect refused"


@pytest.mark.asyncio
async def test_rdap_non_str_registrar_ignored():
    body = {"events": [], "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", ["x"]]]]}]}
    r = await rdap.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(200, json=body)), now=NOW)
    assert r.status == "ok" and "registrar" not in r.summary


@pytest.mark.asyncio
async def test_rdap_ipv6_colons_literal():
    def h(req):
        assert req.url.path == "/ip/2001:db8::1"
        return httpx.Response(200, json={"name": "N"})
    r = await rdap.lookup("ip", "2001:db8::1", transport=T(h), now=NOW)
    assert r.link == "https://rdap.org/ip/2001:db8::1"


@pytest.mark.asyncio
async def test_rdap_too_many_redirects():
    r = await rdap.lookup("domain", "x.com", transport=T(
        lambda req: httpx.Response(302, headers={"location": "https://rdap.example/domain/x.com"})), now=NOW)
    assert r.status == "error" and r.error == "rdap redirect refused"


@pytest.mark.asyncio
async def test_crtsh_young_and_count_and_empty():
    certs = [{"not_before": "2026-09-25T00:00:00"}, {"not_before": "2026-09-28T00:00:00"}]

    def h(req):
        assert str(req.url) == "https://crt.sh/?q=new.com&output=json"
        return httpx.Response(200, json=certs)
    r = await crtsh.lookup("domain", "new.com", transport=T(h), now=NOW)
    assert r.verdict == "suspicious" and r.summary == {"cert_count": 2, "first_cert": "2026-09-25T00:00:00"}
    assert (await crtsh.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(200, json=[])), now=NOW)).status == "not_found"


@pytest.mark.asyncio
async def test_crtsh_502_html():
    r = await crtsh.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(502, text="<html>")), now=NOW)
    assert r.status == "error" and r.error == "crtsh unavailable (HTTP 502)"


def test_registry_complete():
    assert set(REGISTRY) == {"virustotal", "urlhaus", "threatfox", "rdap", "crtsh"}
    assert REGISTRY["crtsh"].TYPES == frozenset({"domain"}) and REGISTRY["rdap"].TYPES == frozenset({"domain", "ip"})
