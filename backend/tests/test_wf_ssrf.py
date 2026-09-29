import socket
import pytest

from app.workflows.ssrf import assert_url_allowed, SSRFError


def fake_resolver(mapping):
    def resolve(host, port, proto=0):
        if host not in mapping:
            raise socket.gaierror("nx")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]
    return resolve


R = fake_resolver({
    "api.example.com": ["93.184.216.34"],
    "sneaky.example.com": ["10.0.0.5"],
    "internal.corp": ["10.1.2.3"],
    "127.0.0.1": ["127.0.0.1"],
    "10.0.0.1": ["10.0.0.1"],
    "169.254.169.254": ["169.254.169.254"],
    "::1": ["::1"],
    "cgnat.example.com": ["100.100.100.200"],
    "100.100.100.200": ["100.100.100.200"],
    "v6.example.com": ["2606:2800:220:1:248:1893:25c8:1946"],
})


def test_public_host_allowed():
    assert assert_url_allowed("https://api.example.com/v1", [], resolve=R) == "93.184.216.34"
    assert assert_url_allowed("https://v6.example.com/", [], resolve=R) == "2606:2800:220:1:248:1893:25c8:1946"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5432/",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://sneaky.example.com/",   # public name, private IP
    "http://100.100.100.200/",      # CGNAT / shared address space (100.64.0.0/10)
    "http://cgnat.example.com/",
])
def test_private_targets_blocked(url):
    with pytest.raises(SSRFError):
        assert_url_allowed(url, [], resolve=R)


def test_allowlisted_host_passes():
    # allowlisted hosts are trusted as named: nothing to pin
    assert assert_url_allowed("http://internal.corp/hook", ["INTERNAL.corp"], resolve=R) is None


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x", "ftp://api.example.com", "http:///nohost"])
def test_bad_scheme_or_host_blocked(url):
    with pytest.raises(SSRFError):
        assert_url_allowed(url, [], resolve=R)


def test_unresolvable_blocked():
    with pytest.raises(SSRFError):
        assert_url_allowed("https://nope.invalid/", [], resolve=R)


def test_bad_port_raises_ssrferror():
    """Port out of range should raise SSRFError, not ValueError."""
    with pytest.raises(SSRFError):
        assert_url_allowed("http://api.example.com:99999/", [], resolve=R)


def test_overlong_label_raises_ssrferror():
    """Hostname label > 63 chars should raise SSRFError, not UnicodeError."""
    overlong_host = "a" * 64 + ".example.com"
    with pytest.raises(SSRFError):
        assert_url_allowed(f"http://{overlong_host}/", [], resolve=socket.getaddrinfo)


def test_allowlist_normalization_trailing_dot():
    """Allowlist entry with trailing dot should match hostname without it."""
    assert_url_allowed("http://internal.corp./hook", ["internal.corp"], resolve=R)


def test_allowlist_normalization_whitespace():
    """Allowlist entry with whitespace should match after normalization."""
    assert_url_allowed("http://internal.corp/hook", [" internal.corp "], resolve=R)
