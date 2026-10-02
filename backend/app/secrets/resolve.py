"""Send-time secret resolution and output redaction.

Only placeholders carrying the render's nonce (`placeholder_re(nonce)`) are resolved; any other
placeholder-looking text is inert. Secret values are never logged.
"""
import re
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

from sqlalchemy import select

from app.models.tenant_secret import TenantSecret
from app.secrets.hosts import host_allowed
from app.secrets.refs import placeholder_re
from app.utils.crypto import decrypt
from app.workflows.nodes import NodeError

MASK = "••••"
_MARK_RE = re.compile("\ue000(\\d+)\ue001")


def _walk(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, fn) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_walk(v, fn) for v in obj)
    return obj


async def resolve_for_request(db, tenant_id, url, headers, body, host, nonce):
    if nonce is None:
        return url, headers, body, {}
    rx = placeholder_re(nonce)
    # The placeholder contains '#', which would confuse urlsplit: swap in delimiter-free index markers first.
    url_names: list[str] = []

    def mark(m):
        url_names.append(m.group(1))
        return f"\ue000{len(url_names) - 1}\ue001"

    # percent-encode our marker delimiters if the URL itself contains them, so data cannot forge a marker
    url = url.replace("\ue000", "%EE%80%80").replace("\ue001", "%EE%80%81")
    parts = urlsplit(rx.sub(mark, url))
    if _MARK_RE.search(parts.scheme) or _MARK_RE.search(parts.netloc):
        raise NodeError("secrets are not allowed in the URL host or credentials")
    scheme, netloc, path, query = parts.scheme, parts.netloc, parts.path, parts.query  # fragment dropped

    # names actually used: path/query markers (a fragment-only placeholder is stripped, not used)
    names: list[str] = []

    def add(n):
        if n not in names:
            names.append(n)

    for seg in (path, query):
        for m in _MARK_RE.finditer(seg):
            add(url_names[int(m.group(1))])
    for v in headers.values():
        for n in rx.findall(v):
            add(n)

    def collect(s):
        for n in rx.findall(s):
            add(n)
        return s

    _walk(body, collect)
    if not names:
        return urlunsplit((scheme, netloc, path, query, "")), headers, body, {}

    rows = {r.name: r for r in (await db.execute(
        select(TenantSecret).where(TenantSecret.tenant_id == tenant_id, TenantSecret.name.in_(names)))).scalars()}
    used: dict[str, str] = {}
    for n in names:
        row = rows.get(n)
        if row is None:
            raise NodeError(f"Unknown secret {n}")
        try:
            used[n] = decrypt(row.value_enc)
        except Exception:
            raise NodeError(f"Secret {n} could not be decrypted — re-enter it in Integrations") from None
        if not host_allowed(host, row.allowed_hosts):
            raise NodeError(f"Secret {n} is not allowed for host {host}")

    def sub(s, enc=lambda v: v):
        return rx.sub(lambda m: enc(used[m.group(1)]), s)

    new_headers = {}
    for k, v in headers.items():
        for n in rx.findall(v):
            if "\r" in used[n] or "\n" in used[n]:
                raise NodeError(f"secret {n} contains a line break and cannot be used in a header")
        new_headers[k] = sub(v)
    def usub(seg, enc):
        return _MARK_RE.sub(lambda m: enc(used[url_names[int(m.group(1))]]), seg)

    new_url = urlunsplit((scheme, netloc, usub(path, lambda v: quote(v, safe="")), usub(query, quote_plus), ""))
    new_body = _walk(body, sub)

    now = datetime.now(timezone.utc)
    for n in used:
        rows[n].last_used_at = now
    return new_url, new_headers, new_body, used


def _variants(used):
    out = set()
    for v in used.values():
        if v:
            out.update({v, quote(v, safe=""), quote_plus(v)})
    return sorted(out, key=len, reverse=True)


def redact_text(s, used):
    for v in _variants(used):
        s = s.replace(v, MASK)
    return s


def redact(obj, used):
    vs = _variants(used)
    if not vs:
        return obj

    def f(s):
        for v in vs:
            s = s.replace(v, MASK)
        return s
    return _walk(obj, f)
