"""Pure host-pattern rules for secret destinations."""
import ipaddress
import re

_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
_HOST = re.compile(rf"^(?=.{{1,253}}$)({_LABEL}\.)+(?=[a-z0-9-]*[a-z])[a-z0-9-]{{2,63}}$")


def _norm(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("["):
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:  # host:port (an IPv6 literal has several colons)
        h = h.split(":", 1)[0]
    return h.rstrip(".")


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
        return bool(_HOST.match(p[2:]))
    if _HOST.match(p):
        return True
    return p in (tenant_allowlist or [])


def host_allowed(host: str, patterns) -> bool:
    h = _norm(host)
    if not h:
        return False
    for p in patterns or []:
        p = (p or "").lower().rstrip(".")
        if p.startswith("*."):
            base = p[2:]
            if h.endswith("." + base) and h != base:
                return True
        elif h == p or (_is_ip(h) and _is_ip(p) and ipaddress.ip_address(h) == ipaddress.ip_address(p)):
            return True
    return False
