import pytest

from app.secrets.hosts import host_allowed, valid_host_pattern


@pytest.mark.parametrize("p", ["api.example.com", "*.example.com", "yourco.atlassian.net", "x-y.example.io"])
def test_valid_patterns(p):
    assert valid_host_pattern(p, [])


@pytest.mark.parametrize("p", ["", "*", "*.com", "example", "http://x.com", "x.com/", "a..b.com", "*.*.x.com",
                               "10.0.0.1", "intranet", "EXAMPLE.COM"])
def test_invalid_patterns_without_allowlist(p):
    assert not valid_host_pattern(p, [])


def test_ip_and_single_label_only_if_on_tenant_allowlist():
    assert valid_host_pattern("10.0.0.1", ["10.0.0.1"]) and valid_host_pattern("intranet", ["intranet"])


@pytest.mark.parametrize("host,patterns,ok", [
    ("api.example.com", ["api.example.com"], True),
    ("API.Example.com.", ["api.example.com"], True),
    ("a.example.com", ["*.example.com"], True),
    ("a.b.example.com", ["*.example.com"], True),
    ("example.com", ["*.example.com"], False),
    ("evil-example.com", ["*.example.com"], False),
    ("example.com.evil.com", ["*.example.com", "example.com"], False),
    ("api.example.com", [], False),
    ("[::1]", ["::1"], True),
    ("api.example.com:8443", ["api.example.com"], True),
])
def test_host_allowed(host, patterns, ok):
    assert host_allowed(host, patterns) is ok


@pytest.mark.parametrize("host", [
    "evil.com@api.example.com", "evil.com/.example.com", ".example.com", "..example.com",
    "a.example.com..", "api.example.com:evil.com", "[::1]evil", " api.example.com\n", "ſ.example.com",
    "0x7f.1", "api.example.com:",
])
def test_malformed_hosts_rejected(host):
    assert host_allowed(host, ["*.example.com", "api.example.com", "example.com"]) is False


@pytest.mark.parametrize("p", ["a.-c", "a.c-", "*.x.-c", "*.", "*"])
def test_hyphen_tld_and_bare_wildcard_invalid(p):
    assert not valid_host_pattern(p, [])


def test_pattern_edge_cases():
    assert host_allowed("api.example.com", ["API.EXAMPLE.COM"]) is False
    assert host_allowed("api.example.com", ["*."]) is False
    assert host_allowed("[::1]:80", ["::1"]) is True
    assert host_allowed("10.0.0.1", ["10.0.0.1"]) is True
    assert host_allowed("intranet", ["intranet"]) is True
