import pytest
from app.enrichment.indicators import normalise, tlp_allows, should_skip


@pytest.mark.parametrize("raw_type,value,expected", [
    ("ip_address", " 8.8.8.8 ", ("ip", "8.8.8.8")),
    ("ip", "2001:DB8:0:0::1", ("ip", "2001:db8::1")),
    ("domain", "Example.COM.", ("domain", "example.com")),
    ("url", "HTTP://Example.COM/a/B?x=1#frag", ("url", "http://example.com/a/B?x=1")),
    ("file_hash", "D41D8CD98F00B204E9800998ECF8427E", ("file_hash", "d41d8cd98f00b204e9800998ecf8427e")),
    ("file_hash", "a" * 64, ("file_hash", "a" * 64)),
])
def test_normalise_ok(raw_type, value, expected):
    assert normalise(raw_type, value) == expected


@pytest.mark.parametrize("raw_type,value", [
    ("ip_address", "999.1.1.1"), ("ip", ""), ("file_hash", "not-a-hash"), ("file_hash", "abc"),
    ("domain", "no spaces.com"), ("domain", ""), ("url", "notaurl"), ("url", "ftp://x.com/a"),
    ("email", "a@b.com"), ("registry_key", "HKLM\\x"), ("mutex", "m"), ("other", "x"),
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
