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
})


def test_public_host_allowed():
    assert_url_allowed("https://api.example.com/v1", [], resolve=R)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5432/",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://sneaky.example.com/",   # public name, private IP
])
def test_private_targets_blocked(url):
    with pytest.raises(SSRFError):
        assert_url_allowed(url, [], resolve=R)


def test_allowlisted_host_passes():
    assert_url_allowed("http://internal.corp/hook", ["INTERNAL.corp"], resolve=R)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x", "ftp://api.example.com", "http:///nohost"])
def test_bad_scheme_or_host_blocked(url):
    with pytest.raises(SSRFError):
        assert_url_allowed(url, [], resolve=R)


def test_unresolvable_blocked():
    with pytest.raises(SSRFError):
        assert_url_allowed("https://nope.invalid/", [], resolve=R)
