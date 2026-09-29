"""Outbound URL guard for the http_request node."""
import ipaddress
import socket
from typing import List
from urllib.parse import urlparse


class SSRFError(ValueError):
    pass


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
            or ip.is_multicast or ip.is_reserved)


def assert_url_allowed(url: str, allowlist: List[str], resolve=socket.getaddrinfo) -> None:
    # ponytail: checks resolved IPs, then httpx resolves again (DNS-rebinding TOCTOU window).
    # Upgrade path: pin the vetted IP via a custom httpx transport if this ever matters.
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise SSRFError(f"scheme '{p.scheme}' not allowed")
    host = p.hostname
    if not host:
        raise SSRFError("URL has no host")
    if host.lower() in {h.lower() for h in allowlist}:
        return
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        infos = resolve(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SSRFError(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if _blocked(ip):
            raise SSRFError(f"{host} resolves to a non-public address ({ip}); add it to the HTTP allowlist to permit it")
