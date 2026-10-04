"""Pure host-pattern rules for secret destinations."""
import ipaddress
import re

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_TLD = r"(?=[a-z0-9-]*[a-z])(?!-)[a-z0-9-]{2,63}(?<!-)"
_HOST = re.compile(rf"^(?=.{{1,253}}$)({_LABEL}\.)+{_TLD}$")
_BAD_CHARS = re.compile(r"[\s\x00-\x1f\x7f@/?#\\]")
_BRACKETED = re.compile(r"^\[([^\]]*)\](?::[0-9]+)?$")


def _norm(host: str):
    """Lowercase, drop a port / IPv6 brackets / one trailing dot. None if malformed."""
    if not isinstance(host, str) or not host or _BAD_CHARS.search(host):
        return None
    h = host.lower()
    m = _BRACKETED.match(h)
    if m:
        h = m.group(1)
    elif h.startswith("[") or "]" in h:
        return None
    elif h.count(":") == 1:  # host:port (an IPv6 literal has several colons)
        h, port = h.split(":", 1)
        if not port.isascii() or not port.isdigit():
            return None
    if h.endswith("."):
        h = h[:-1]
    if not h or h.startswith(".") or ".." in h:
        return None
    return h


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def valid_host_pattern(p: str, tenant_allowlist) -> bool:
    if not isinstance(p, str) or p != p.strip() or p != p.lower():
        return False
    if p.startswith("*."):
        return bool(_HOST.fullmatch(p[2:]))
    if _HOST.fullmatch(p):
        return True
    return p in (tenant_allowlist or [])


def host_allowed(host: str, patterns) -> bool:
    """Fail-closed match. Callers must pass the parsed hostname (e.g. urlsplit().hostname),
    not a raw netloc or URL; anything malformed is rejected."""
    h = _norm(host)
    if h is None:
        return False
    pats = [p for p in (patterns or []) if isinstance(p, str)]
    if not (_HOST.fullmatch(h) or _is_ip(h) or ("." not in h and h in pats)):
        return False
    for p in pats:
        if not valid_host_pattern(p, pats):
            continue
        if p.startswith("*."):
            base = p[2:]
            if h.endswith("." + base) and h != base:
                return True
        elif h == p or (_is_ip(h) and _is_ip(p) and ipaddress.ip_address(h) == ipaddress.ip_address(p)):
            return True
    return False
