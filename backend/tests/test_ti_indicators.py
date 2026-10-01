import pytest
from app.enrichment.indicators import normalise, tlp_allows, should_skip


@pytest.mark.parametrize("raw_type,value,expected", [
    ("ip_address", " 8.8.8.8 ", ("ip", "8.8.8.8")),
    ("ip", "2001:DB8:0:0::1", ("ip", "2001:db8::1")),
    ("domain", "Example.COM.", ("domain", "example.com")),
    ("url", "HTTP://Example.COM/a/B?x=1#frag", ("url", "http://example.com/a/B?x=1")),
    ("file_hash", "D41D8CD98F00B204E9800998ECF8427E", ("file_hash", "d41d8cd98f00b204e9800998ecf8427e")),
    ("file_hash", "a" * 64, ("file_hash", "a" * 64)),
    ("url", "http://u:p@Example.com/a", ("url", "http://example.com/a")),
])
def test_normalise_ok(raw_type, value, expected):
    assert normalise(raw_type, value) == expected


@pytest.mark.parametrize("raw_type,value", [
    ("ip_address", "999.1.1.1"), ("ip", ""), ("file_hash", "not-a-hash"), ("file_hash", "abc"),
    ("domain", "no spaces.com"), ("domain", ""), ("url", "notaurl"), ("url", "ftp://x.com/a"),
    ("email", "a@b.com"), ("registry_key", "HKLM\\x"), ("mutex", "m"), ("other", "x"),
    ("url", "http://[abc/"), ("domain", "10.0.0.11"), ("domain", "1.2.3.4"),
])
def test_normalise_rejects(raw_type, value):
    assert normalise(raw_type, value) is None


def test_tlp_allows():
    assert tlp_allows("white", "green") and tlp_allows("green", "green")
    assert not tlp_allows("amber", "green") and not tlp_allows("red", "amber")
    assert not tlp_allows("white", "none")
    assert not tlp_allows("bogus", "red")


@pytest.mark.parametrize("itype,value", [
    ("ip", "10.1.2.3"), ("ip", "127.0.0.1"), ("ip", "169.254.1.1"), ("ip", "::1"), ("ip", "224.0.0.1"),
    ("ip", "0.0.0.0"), ("domain", "corp.local"), ("domain", "a.b.corp.local"), ("url", "https://x.corp.local/p"),
])
def test_should_skip(itype, value):
    assert should_skip(itype, value, ["corp.local"])


def test_should_not_skip_public():
    assert not should_skip("ip", "8.8.8.8", ["corp.local"])
    assert not should_skip("domain", "notcorp.local.evil.com", ["corp.local"])
    assert not should_skip("file_hash", "a" * 32, ["corp.local"])


@pytest.mark.parametrize("url", [
    "http://0x7f.1/", "http://2130706433/", "http://127.1/", "http://017700000001/",
    "http://localhost/x", "http://intranet/x", "http://[::1]/",
])
def test_should_skip_disguised_url_hosts(url):
    assert should_skip("url", url, ["corp.local"])


def test_should_not_skip_public_url():
    assert not should_skip("url", "http://8.8.8.8/", ["corp.local"])


@pytest.mark.parametrize("value", [
    "host.local", "foo.localhost", "svc.internal", "nas.lan", "printer.home.arpa", "dc1.corp",
    "wiki.intranet", "site.test", "x.invalid", "www.example", "local",
])
def test_special_use_domains_skipped(value):
    assert should_skip("domain", value, []) is True
    assert should_skip("url", f"https://{value}/p", []) is True


@pytest.mark.parametrize("value", ["example.com", "localcorp.com", "test.io"])
def test_special_use_lookalikes_not_skipped(value):
    assert should_skip("domain", value, []) is False
    assert should_skip("url", f"https://{value}/p", []) is False


def test_httpx_logger_pinned_to_warning():
    import logging
    import app.enrichment.sources.base  # noqa: F401
    assert logging.getLogger("httpx").level >= logging.WARNING
