"""Outbound URL guard for tenant-directed HTTP: the http_request node, OAuth token URLs, the tenant AI
api_base, and (with an operator-set list) OIDC discovery."""
import hashlib
import ipaddress
import logging
import re
import socket
from typing import List, Optional
from urllib.parse import urlparse

from app.core.config import settings

_resolve = socket.getaddrinfo  # module-level hook so tests can stub DNS
logger = logging.getLogger(__name__)

MAX_ALLOWLIST = 100
HTTPS_ONLY = "secrets are only sent over https"


class SSRFError(ValueError):
    pass


class AllowlistError(ValueError):
    def __init__(self, entry, reason: str):
        super().__init__(f"Invalid allowlist entry {entry!r}: {reason}")
        self.entry = entry


_SHARED = ipaddress.ip_network("100.64.0.0/10")  # CGNAT; IPv4Address.is_shared doesn't exist on py3.11
_THIS_NET = ipaddress.ip_network("0.0.0.0/8")    # Linux routes 0.x.x.x to the local host
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_EC2_V6_METADATA = ipaddress.ip_address("fd00:ec2::254")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _effective(ip):
    """The IPv4 address an IPv6 address really reaches (mapped, compatible, NAT64, 6to4, Teredo), else ip."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return ip.ipv4_mapped
        if int(ip) >> 32 == 0 or ip in _NAT64:  # ::a.b.c.d (incl. :: and ::1) and 64:ff9b::a.b.c.d
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip.sixtofour:
            return ip.sixtofour
        if ip.teredo:
            return ip.teredo[1]
    return ip


def _always_blocked(ip) -> bool:
    """Never reachable, allowlisted or not: loopback, link-local (incl. cloud metadata), unspecified,
    multicast, reserved."""
    a = _effective(ip)
    return (a.is_loopback or a.is_link_local or a.is_unspecified or a.is_multicast or a.is_reserved
            or a == _EC2_V6_METADATA or (isinstance(a, ipaddress.IPv4Address) and a in _THIS_NET))


def _private(ip) -> bool:
    a = _effective(ip)
    return (a.is_private or (isinstance(a, ipaddress.IPv4Address) and a in _SHARED)
            or (isinstance(a, ipaddress.IPv6Address) and a.is_site_local))  # deprecated fec0::/10


def allowlist_entry(raw) -> Optional[str]:
    """Normalized entry (lowercase, no trailing dot), or None if it isn't a multi-label DNS hostname or a
    `*.suffix` wildcard over one. Rejects IP literals, single-label names (redis, db, localhost), schemes,
    ports, paths and userinfo."""
    if not isinstance(raw, str):
        return None
    e = raw.strip().lower()
    e = e[:-1] if e.endswith(".") else e
    if not e or len(e) > 253:
        return None
    name = e[2:] if e.startswith("*.") else e
    labels = name.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(lb) for lb in labels):
        return None
    if labels[-1].isdigit() or labels[-1] == "localhost":
        return None
    try:
        socket.inet_aton(name)  # numeric forms the resolver would still treat as an IP (0x7f.0x1)
        return None
    except OSError:
        pass
    return e


def normalize_allowlist(entries) -> List[str]:
    """Validated, deduplicated, sorted allowlist. Raises AllowlistError naming the first bad entry."""
    out = set()
    for raw in entries:
        if isinstance(raw, str) and not raw.strip():
            continue
        e = allowlist_entry(raw)
        if e is None:
            raise AllowlistError(raw, "use a DNS hostname like api.example.com or a wildcard like "
                                      "*.corp.example.com (no IP address, scheme, port, path or single-label name)")
        out.add(e)
    if len(out) > MAX_ALLOWLIST:
        raise AllowlistError(f"{len(out)} entries", f"at most {MAX_ALLOWLIST} entries")
    return sorted(out)


def stored_entry(raw) -> Optional[str]:
    """An entry as saved before validation existed (e.g. `https://api.example.com/path`), reduced to its
    host and validated: scheme, userinfo, path, query and port are stripped. None if still invalid."""
    return allowlist_entry(_host_part(raw)) if isinstance(raw, str) else None


def _host_part(raw: str) -> str:
    s = raw.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = re.split(r"[/?#]", s, maxsplit=1)[0].rpartition("@")[2]
    if s.count(":") == 1:  # host:port (IPv6 literals have more colons and are rejected anyway)
        s = s.split(":", 1)[0]
    return s


_LOGGABLE = re.compile(r"[A-Za-z0-9.*\-]{1,253}")


def _redacted(raw) -> str:
    """Safe-to-log form of a stored entry: its bare host part, never userinfo/path/query; else a short hash."""
    s = raw if isinstance(raw, str) else repr(raw)
    host = _host_part(s)
    if "@" not in s and _LOGGABLE.fullmatch(host):  # anything with userinfo is hashed, however it's shaped
        return host
    return f"<len={len(s)} sha256={hashlib.sha256(s.encode()).hexdigest()[:8]}>"


def stored_allowlist(entries, tenant_id=None) -> List[str]:
    """The tenant's stored allowlist in normalized form; invalid legacy entries are dropped and logged."""
    if entries is None:
        return []
    if not isinstance(entries, list):
        logger.warning("http allowlist: stored value is not a list, ignoring it tenant=%s", tenant_id)
        return []
    out = set()
    for raw in entries:
        e = stored_entry(raw)
        if e is None:
            logger.warning("http allowlist: dropping invalid entry tenant=%s host=%s", tenant_id, _redacted(raw))
        else:
            out.add(e)
    return sorted(out)


def host_on_allowlist(host: Optional[str], allowlist) -> bool:
    """Case- and trailing-dot-insensitive match against the tenant HTTP allowlist. `*.suffix` matches strict
    subdomains. Stored legacy entries match as their normalized form (`stored_entry`); invalid ones never do."""
    if not host or not isinstance(allowlist, (list, tuple)):
        return False
    h = host.strip().rstrip(".").lower()
    for e in filter(None, (stored_entry(x) for x in allowlist)):
        if e.startswith("*.") and h.endswith(e[1:]) and h != e[2:]:
            return True
        if h == e:
            return True
    return False


def _operator_listed(host: str, hosts) -> bool:
    h = host.strip().rstrip(".").lower()
    return h in {x.strip().rstrip(".").lower() for x in (hosts or []) if isinstance(x, str)}


def assert_url_allowed(url: str, allowlist: List[str], resolve=None, *, operator_hosts: bool = False) -> str:
    """Raise SSRFError unless the URL may be fetched; return the vetted IP the caller must connect to
    (so a second DNS answer can't rebind the request).

    Every address the host resolves to is checked, allowlisted or not: loopback, link-local/metadata,
    unspecified, multicast and reserved are always refused. Private ranges are allowed only for a listed
    host, and for a tenant allowlist only when the platform sets WORKFLOW_ALLOW_PRIVATE_ALLOWLIST.
    `operator_hosts=True` marks `allowlist` as a deployment-level list (OIDC_PRIVATE_HOSTS): exact-match
    names, private ranges allowed.
    """
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise SSRFError(f"scheme '{p.scheme}' not allowed")
    host = p.hostname
    if not host:
        raise SSRFError("URL has no host")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError as e:
        raise SSRFError("invalid port in URL") from e

    try:
        infos = (resolve or _resolve)(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, ValueError, UnicodeError, OSError) as e:
        raise SSRFError(f"cannot resolve {host}") from e

    ips = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not ips:
        raise SSRFError(f"cannot resolve {host}")
    if operator_hosts:
        listed, private_ok = _operator_listed(host, allowlist), True
    else:
        listed, private_ok = host_on_allowlist(host, allowlist), settings.WORKFLOW_ALLOW_PRIVATE_ALLOWLIST
    for ip in ips:
        if _always_blocked(ip):
            raise SSRFError(f"{host} resolves to a non-public address ({ip}) that is never allowed")
        if _private(ip):
            if not listed:
                raise SSRFError(f"{host} resolves to a non-public address ({ip}); add it to the HTTP allowlist "
                                "to permit it (private addresses also need WORKFLOW_ALLOW_PRIVATE_ALLOWLIST)"
                                if not operator_hosts else f"{host} resolves to a non-public address ({ip})")
            if not private_ok:
                raise SSRFError(f"{host} resolves to a private address ({ip}); this platform does not let "
                                "allowlisted hosts reach private networks (WORKFLOW_ALLOW_PRIVATE_ALLOWLIST)")
    return str(ips[0])
