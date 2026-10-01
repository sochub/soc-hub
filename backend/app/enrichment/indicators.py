"""Pure indicator helpers: normalisation, TLP gating and never-send-out rules."""
import ipaddress
import re
import socket
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

TLP_ORDER = ("white", "green", "amber", "red")
_TYPE_MAP = {"ip_address": "ip", "ip": "ip", "domain": "domain", "url": "url", "file_hash": "file_hash"}
_DOMAIN_RX = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
_HASH_RX = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$")


def _domain(v: str) -> Optional[str]:
    v = v.strip().lower().rstrip(".")
    if not _DOMAIN_RX.match(v) or not re.search(r"[a-z]", v.rsplit(".", 1)[-1]):
        return None  # all-digit TLD would be a disguised IPv4
    return v


def normalise(raw_type: str, value: str) -> Optional[Tuple[str, str]]:
    itype = _TYPE_MAP.get((raw_type or "").lower())
    v = (value or "").strip()
    if not itype or not v:
        return None
    if itype == "ip":
        try:
            return itype, ipaddress.ip_address(v).compressed
        except ValueError:
            return None
    if itype == "domain":
        d = _domain(v)
        return (itype, d) if d else None
    if itype == "file_hash":
        h = v.lower()
        return (itype, h) if _HASH_RX.match(h) else None
    try:
        parts = urlsplit(v)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1].lower()
    return itype, urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def tlp_allows(tlp: str, max_tlp: str) -> bool:
    if tlp not in TLP_ORDER or max_tlp not in TLP_ORDER:
        return False
    return TLP_ORDER.index(tlp) <= TLP_ORDER.index(max_tlp)


# Special-use / private-use names (RFC 6761, 6762, 8375 and common internal TLDs) never leave the
# system, whether or not the tenant lists them in internal_domains.
_SPECIAL_USE = ("local", "localhost", "internal", "lan", "home.arpa", "corp", "intranet", "test", "invalid",
                "example")


def _under(host: str, internal_domains) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (x.lower().strip(".") for x in internal_domains if x))


def should_skip(itype: str, value: str, internal_domains) -> bool:
    """True when the value must never leave the system (private/reserved IPs, internal domains)."""
    if itype == "ip":
        ip = ipaddress.ip_address(value)
        return not ip.is_global or ip.is_multicast
    if itype == "domain":
        return _under(value, (*_SPECIAL_USE, *internal_domains))
    if itype == "url":
        try:
            host = (urlsplit(value).hostname or "").rstrip(".")
        except ValueError:
            return True
        return _host_blocked(host, internal_domains)
    return False


def _non_global(ip) -> bool:
    return not ip.is_global or ip.is_multicast


def _host_blocked(host: str, internal_domains) -> bool:
    try:
        return _non_global(ipaddress.ip_address(host))
    except ValueError:
        pass
    if re.fullmatch(r"[0-9a-fA-FxX.]+", host):
        try:  # numeric/hex/octal shorthand such as 0x7f.1, 127.1, 2130706433
            return _non_global(ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(host))))
        except OSError:
            pass
    if not host or "." not in host:
        return True  # single-label hosts (localhost, intranet) are internal
    return _under(host, (*_SPECIAL_USE, *internal_domains))
