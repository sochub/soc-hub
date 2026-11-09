"""Shared SSRF host guard for tenant-supplied outbound hosts."""
import ipaddress
import socket
from typing import Iterable, Optional


def validate_outbound_host(host: str, allowlist: Iterable[str], setting_name: str, resolve=None) -> Optional[str]:
    """Return an error message if `host` resolves to a non-public address, else None.

    Exact hosts in `allowlist` are allowed. We later connect by hostname, so a DNS-rebinding race between
    this check and the connect is an accepted trade-off.
    """
    if host in allowlist:
        return None
    try:
        infos = (resolve or (lambda h: socket.getaddrinfo(h, None)))(host)
    except OSError:
        return "host could not be resolved"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return "host could not be resolved"
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified):
            return f"host resolves to a private address; add it to {setting_name} to allow"
    return None
