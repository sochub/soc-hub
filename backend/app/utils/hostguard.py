"""Shared SSRF host guard for tenant-supplied outbound hosts."""
import ipaddress
import socket
from typing import Iterable, Optional, Tuple


def validate_outbound_host(host: str, allowlist: Iterable[str], setting_name: str, resolve=None) -> Optional[str]:
    """Return an error message if `host` resolves to a non-public address, else None (save-time check).

    Exact hosts in `allowlist` are allowed without resolving. Connections must go through `vetted_ip`, which
    returns the address to connect to so a second DNS answer can't rebind them.
    """
    if host in allowlist:
        return None
    return vetted_ip(host, allowlist, setting_name, resolve=resolve)[0]


def vetted_ip(host: str, allowlist: Iterable[str], setting_name: str, resolve=None) -> Tuple[Optional[str], Optional[str]]:
    """(error, ip): resolve `host` once and return the address the caller must connect to. Every answer must be
    public unless the host is in `allowlist` (exact match: private addresses allowed)."""
    try:
        infos = (resolve or (lambda h: socket.getaddrinfo(h, None)))(host)
    except OSError:
        return "host could not be resolved", None
    ips = []
    for info in infos:
        try:
            ips.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except ValueError:
            return "host could not be resolved", None
    if not ips:
        return "host could not be resolved", None
    if host not in allowlist:
        for ip in ips:
            # not is_global also covers CGNAT 100.64.0.0/10 and other special-use ranges
            if not ip.is_global or ip.is_multicast:
                return f"host resolves to a private address; add it to {setting_name} to allow", None
    return None, str(ips[0])
