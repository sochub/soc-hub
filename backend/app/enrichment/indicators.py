"""Pure indicator helpers: normalisation, TLP gating and never-send-out rules."""
import ipaddress
import re
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

TLP_ORDER = ("white", "green", "amber", "red")
_TYPE_MAP = {"ip_address": "ip", "ip": "ip", "domain": "domain", "url": "url", "file_hash": "file_hash"}
_DOMAIN_RX = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
_HASH_RX = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$")


def _domain(v: str) -> Optional[str]:
    v = v.strip().lower().rstrip(".")
    return v if _DOMAIN_RX.match(v) else None


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
    parts = urlsplit(v)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1].lower()
    return itype, urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def tlp_allows(tlp: str, max_tlp: str) -> bool:
    if tlp not in TLP_ORDER or max_tlp not in TLP_ORDER:
        return False
    return TLP_ORDER.index(tlp) <= TLP_ORDER.index(max_tlp)


def _under(host: str, internal_domains) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (x.lower().strip(".") for x in internal_domains if x))


def should_skip(itype: str, value: str, internal_domains) -> bool:
    """True when the value must never leave the system (private/reserved IPs, internal domains)."""
    if itype == "ip":
        ip = ipaddress.ip_address(value)
        return not ip.is_global or ip.is_multicast
    if itype == "domain":
        return _under(value, internal_domains)
    if itype == "url":
        host = urlsplit(value).hostname or ""
        try:
            ip = ipaddress.ip_address(host)
            return not ip.is_global or ip.is_multicast
        except ValueError:
            return _under(host, internal_domains)
    return False
