import re


def defang(value: str, itype: str) -> str:
    t = (itype or "").lower()
    v = value or ""
    if t == "url":
        v = re.sub(r"^https", "hxxps", v, flags=re.I) if v.lower().startswith("https") else re.sub(r"^http", "hxxp", v, flags=re.I)
        scheme, sep, rest = v.partition("://")
        host, slash, path = rest.partition("/")
        return f"{scheme}{sep}{host.replace('.', '[.]')}{slash}{path}" if sep else v.replace(".", "[.]")
    if t == "domain":
        return v.replace(".", "[.]")
    if t in ("ip", "ip_address"):
        if ":" in v:
            return v.replace(":", "[:]")
        head, dot, tail = v.rpartition(".")
        return f"{head}[.]{tail}" if dot else v
    if t == "email":
        local, at, dom = v.partition("@")
        return f"{local}[@]{dom.replace('.', '[.]')}" if at else v
    return v
