"""Outbound URL guard for the http_request node."""
import ipaddress
import socket
from typing import List, Optional
from urllib.parse import urlparse


class SSRFError(ValueError):
    pass


_SHARED = ipaddress.ip_network("100.64.0.0/10")  # CGNAT; IPv4Address.is_shared doesn't exist on py3.11


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
            or ip.is_multicast or ip.is_reserved
            or (isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED))


def host_on_allowlist(host: Optional[str], allowlist) -> bool:
    """Exact (case-insensitive, trailing-dot-insensitive) match against the tenant HTTP allowlist."""
    if not host:
        return False
    return host.strip().rstrip(".").lower() in {h.strip().rstrip(".").lower() for h in (allowlist or []) if isinstance(h, str)}


def assert_url_allowed(url: str, allowlist: List[str], resolve=socket.getaddrinfo) -> Optional[str]:
    """Raise SSRFError unless every address the host resolves to is public.

    Returns the vetted IP the caller must connect to (so a second DNS answer can't rebind the
    request to an internal address), or None for an allowlisted host, which is trusted by name.
    """
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise SSRFError(f"scheme '{p.scheme}' not allowed")
    host = p.hostname
    if not host:
        raise SSRFError("URL has no host")

    if host_on_allowlist(host, allowlist):
        return None

    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError as e:
        raise SSRFError(f"invalid port in URL") from e

    try:
        infos = resolve(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, ValueError, UnicodeError, OSError) as e:
        raise SSRFError(f"cannot resolve {host}") from e

    ips = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not ips:
        raise SSRFError(f"cannot resolve {host}")
    for ip in ips:
        if _blocked(ip):
            raise SSRFError(f"{host} resolves to a non-public address ({ip}); add it to the HTTP allowlist to permit it")
    return str(ips[0])
