import re


def defang(value: str, itype: str) -> str:
    t = (itype or "").lower()
    v = "" if value is None else str(value)
    if t == "url":
        v = re.sub(r"^https", "hxxps", v, flags=re.I) if v.lower().startswith("https") else re.sub(r"^http", "hxxp", v, flags=re.I)
        scheme, sep, rest = v.partition("://")
        host, slash, path = rest.partition("/")
        if sep and host.startswith("["):
            end = host.find("]")
            end = len(host) - 1 if end < 0 else end
            host = host[: end + 1].replace(":", "[:]") + host[end + 1:]
        elif sep:
            host = host.replace(".", "[.]")
        return f"{scheme}{sep}{host}{slash}{path}" if sep else v.replace(".", "[.]")
    if t in ("domain", "hostname", "fqdn"):
        return v.replace(".", "[.]")
    if t in ("ip", "ip_address", "ipv4", "ipv6"):
        if ":" in v:
            return v.replace(":", "[:]")
        head, dot, tail = v.rpartition(".")
        return f"{head}[.]{tail}" if dot else v
    if t == "email":
        local, at, dom = v.partition("@")
        return f"{local}[@]{dom.replace('.', '[.]')}" if at else v
    return v
