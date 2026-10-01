import base64
import json

import httpx
import pytest

from app.enrichment.sources import REGISTRY, abusech, virustotal


def transport(handler):
    return httpx.MockTransport(handler)


def vt_body(mal, sus=0, harmless=0, undetected=0, label=None):
    attrs = {"last_analysis_stats": {"malicious": mal, "suspicious": sus, "harmless": harmless, "undetected": undetected},
             "reputation": -5, "tags": ["a", "b", "c", "d", "e", "f"]}
    if label:
        attrs["popular_threat_classification"] = {"suggested_threat_label": label}
    return {"data": {"attributes": attrs}}


@pytest.mark.asyncio
@pytest.mark.parametrize("stats,verdict", [((3,), "malicious"), ((2,), "suspicious"), ((0, 1), "suspicious"),
                                           ((0, 0, 5, 60), "harmless")])
async def test_vt_verdicts(stats, verdict):
    seen = {}

    def h(req):
        seen["url"], seen["key"] = str(req.url), req.headers.get("x-apikey")
        return httpx.Response(200, json=vt_body(*stats))
    r = await virustotal.lookup("ip", "8.8.8.8", "K", transport=transport(h))
    assert r.status == "ok" and r.verdict == verdict
    assert seen == {"url": "https://www.virustotal.com/api/v3/ip_addresses/8.8.8.8", "key": "K"}
    assert len(r.summary["tags"]) == 5 and r.link == "https://www.virustotal.com/gui/ip-address/8.8.8.8"


@pytest.mark.asyncio
async def test_vt_url_id_and_label_and_score():
    url = "http://example.com/a"
    want = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")

    def h(req):
        assert str(req.url).endswith(f"/api/v3/urls/{want}")
        return httpx.Response(200, json=vt_body(45, 0, 10, 15, label="trojan.emotet/x"))
    r = await virustotal.lookup("url", url, "K", transport=transport(h))
    assert r.score == "45/70" and r.summary["popular_threat_label"] == "trojan.emotet/x"


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status,err", [(404, "not_found", None), (401, "error", "invalid API key"),
                                             (429, "rate_limited", None), (503, "error", "virustotal unavailable (HTTP 503)")])
async def test_vt_status_codes(code, status, err):
    r = await virustotal.lookup("domain", "example.com", "K", transport=transport(lambda req: httpx.Response(code)))
    assert r.status == status and r.error == err


@pytest.mark.asyncio
async def test_vt_malformed_body():
    r = await virustotal.lookup("file_hash", "a" * 64, "K",
                                transport=transport(lambda req: httpx.Response(200, text="<html>portal</html>")))
    assert r.status == "error" and "unexpected response" in r.error


@pytest.mark.asyncio
async def test_urlhaus_host_hit_and_miss():
    def hit(req):
        assert str(req.url) == "https://urlhaus-api.abuse.ch/v1/host/" and req.headers["Auth-Key"] == "AB"
        assert req.content == b"host=example.com"
        return httpx.Response(200, json={"query_status": "ok", "urls": [
            {"threat": "malware_download", "url_status": "online", "tags": ["exe"], "date_added": "2026-09-01"}]})
    r = await abusech.lookup_urlhaus("domain", "example.com", "AB", transport=transport(hit))
    assert r.status == "ok" and r.verdict == "malicious" and r.summary["threat"] == "malware_download"
    miss = transport(lambda req: httpx.Response(200, json={"query_status": "no_results"}))
    assert (await abusech.lookup_urlhaus("url", "http://x.com/", "AB", transport=miss)).status == "not_found"


@pytest.mark.asyncio
async def test_urlhaus_payload_for_hash():
    def h(req):
        assert str(req.url) == "https://urlhaus-api.abuse.ch/v1/payload/" and req.content == b"sha256_hash=" + b"a" * 64
        return httpx.Response(200, json={"query_status": "no_results"})
    assert (await abusech.lookup_urlhaus("file_hash", "a" * 64, "AB", transport=transport(h))).status == "not_found"


@pytest.mark.asyncio
async def test_threatfox_hit_hash_and_miss():
    def h(req):
        body = json.loads(req.content)
        assert body == {"query": "search_hash", "hash": "a" * 32}
        return httpx.Response(200, json={"query_status": "ok", "data": [
            {"malware_printable": "Emotet", "threat_type": "payload", "confidence_level": 75, "first_seen": "x", "tags": None},
            {"malware_printable": "Emotet", "threat_type": "payload", "confidence_level": 90, "first_seen": "y", "tags": ["t"]}]})
    r = await abusech.lookup_threatfox("file_hash", "a" * 32, "AB", transport=transport(h))
    assert r.status == "ok" and r.verdict == "malicious" and r.score == "confidence 90"
    assert r.summary["malware_printable"] == "Emotet"
    miss = transport(lambda req: httpx.Response(200, json={"query_status": "no_result"}))
    assert (await abusech.lookup_threatfox("ip", "1.2.3.4", "AB", transport=miss)).status == "not_found"


@pytest.mark.asyncio
async def test_timeout_is_error():
    def boom(req):
        raise httpx.ReadTimeout("slow")
    r = await virustotal.lookup("ip", "8.8.8.8", "K", transport=transport(boom))
    assert r.status == "error" and r.error == "virustotal timed out"


def test_registry():
    assert {"virustotal", "urlhaus", "threatfox"} <= set(REGISTRY)
    assert REGISTRY["virustotal"].TYPES == frozenset({"ip", "domain", "url", "file_hash"})


# ---- fix round 1 ----
KEY = "SECRETKEY123"


def _all_lookups(t):
    return [
        ("vt", lambda: virustotal.lookup("ip", "8.8.8.8", KEY, transport=t)),
        ("uh", lambda: abusech.lookup_urlhaus("domain", "example.com", KEY, transport=t)),
        ("tf", lambda: abusech.lookup_threatfox("ip", "1.2.3.4", KEY, transport=t)),
    ]


@pytest.mark.asyncio
async def test_vt_other_4xx_is_error():
    r = await virustotal.lookup("ip", "8.8.8.8", "K", transport=transport(lambda req: httpx.Response(400)))
    assert r.status == "error" and r.error == "virustotal unavailable (HTTP 400)"


@pytest.mark.asyncio
async def test_urlhaus_404_is_error_not_not_found():
    r = await abusech.lookup_urlhaus("domain", "example.com", "AB", transport=transport(lambda req: httpx.Response(404)))
    assert r.status == "error" and r.error == "urlhaus unavailable (HTTP 404)"
    r = await abusech.lookup_threatfox("ip", "1.2.3.4", "AB", transport=transport(lambda req: httpx.Response(404)))
    assert r.status == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"data": []}, {"data": {"attributes": {"last_analysis_stats": {"malicious": "x"}}}}])
async def test_vt_malformed_200_is_error(body):
    r = await virustotal.lookup("ip", "8.8.8.8", "K", transport=transport(lambda req: httpx.Response(200, json=body)))
    assert r.status == "error" and r.error == "unexpected response from virustotal"


@pytest.mark.asyncio
async def test_threatfox_non_dict_row_is_error():
    t = transport(lambda req: httpx.Response(200, json={"query_status": "ok", "data": ["oops"]}))
    r = await abusech.lookup_threatfox("ip", "1.2.3.4", "AB", transport=t)
    assert r.status == "error" and r.error == "unexpected response from threatfox"


@pytest.mark.asyncio
async def test_redirect_not_followed_is_error():
    t = transport(lambda req: httpx.Response(302, headers={"location": "http://evil.example/"}))
    for _, call in _all_lookups(t):
        assert (await call()).status == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status", [(401, "error"), (429, "rate_limited")])
async def test_abusech_status_mapping(code, status):
    t = transport(lambda req: httpx.Response(code))
    for _, call in _all_lookups(t)[1:]:
        assert (await call()).status == status


@pytest.mark.asyncio
async def test_abusech_timeout_and_threatfox_header():
    def boom(req):
        raise httpx.ReadTimeout("slow")
    r = await abusech.lookup_urlhaus("ip", "1.2.3.4", "AB", transport=transport(boom))
    assert r.error == "urlhaus timed out"
    r = await abusech.lookup_threatfox("ip", "1.2.3.4", "AB", transport=transport(boom))
    assert r.error == "threatfox timed out"
    seen = {}

    def h(req):
        seen["k"] = req.headers.get("Auth-Key")
        return httpx.Response(200, json={"query_status": "no_result"})
    await abusech.lookup_threatfox("ip", "1.2.3.4", "AB", transport=transport(h))
    assert seen["k"] == "AB"


@pytest.mark.asyncio
async def test_value_quoted_and_tags_must_be_list():
    seen = {}

    def h(req):
        seen["raw"] = req.url.raw_path.decode()
        return httpx.Response(200, json=vt_body(0, 0, 1, 1) | {"data": {"attributes": {
            "last_analysis_stats": {"harmless": 1}, "tags": "notalist"}}})
    r = await virustotal.lookup("domain", "a/b?c#d", "K", transport=transport(h))
    assert "a%2Fb%3Fc%23d" in seen["raw"] and "a%2Fb%3Fc%23d" in r.link
    assert r.summary["tags"] == []
    t = transport(lambda req: httpx.Response(200, json={"query_status": "ok", "urls": [{"tags": "x"}]}))
    r = await abusech.lookup_urlhaus("domain", "a&b", "AB", transport=t)
    assert r.summary["tags"] == [] and r.link.endswith("search=a%26b")


@pytest.mark.asyncio
async def test_key_never_leaks_into_results():
    cases = [
        httpx.Response(200, json=vt_body(5, 0, 1, 1)),
        httpx.Response(200, json={"query_status": "ok", "urls": [{"threat": "t"}], "data": [{"confidence_level": 1}]}),
        httpx.Response(200, text="not json"), httpx.Response(401), httpx.Response(429), httpx.Response(503),
        httpx.Response(404), httpx.Response(302),
    ]
    for resp in cases:
        t = transport(lambda req, resp=resp: resp)
        for _, call in _all_lookups(t):
            r = await call()
            assert KEY not in json.dumps([r.summary, r.link, r.error])
