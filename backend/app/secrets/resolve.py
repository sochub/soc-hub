"""Send-time secret resolution and output redaction.

Only placeholders carrying the render's nonce (`placeholder_re(nonce)`) are resolved; any other
placeholder-looking text is inert. Secret values are never logged.
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

from sqlalchemy import select, update

from app.models.tenant_secret import TenantSecret
from app.secrets.hosts import host_allowed
from app.secrets.refs import placeholder_re
from app.utils.crypto import decrypt
from app.workflows.errors import NodeError
from app.workflows.ssrf import host_on_allowlist

MASK = "••••"
_MARK_RE = re.compile("\ue000(\\d+)\ue001")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
def header_value_problem(v: str, n: str = "value"):
    """Why `v` can't go in an HTTP header (None if fine). Shared with the plaintext-credential scan."""
    if "\r" in v or "\n" in v:
        return f"secret {n} contains a line break and cannot be used in a header"
    if v != v.strip() or not v.isascii():
        return f"secret {n} has leading/trailing whitespace or non-ASCII characters and cannot be used in a header"
    if _CTRL_RE.search(v):
        return f"secret {n} contains a control character and cannot be used in a header"
    return None


_URL_ERR = "secrets are not allowed in the URL host or credentials"


def _body_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _body_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _body_keys(v)


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


async def resolve_for_request(db, tenant_id, url, headers, body, host, nonce, names, allowlist=()):
    """Substitute this render's placeholders. `names`: the names the render's SecretsNamespace handed
    out (R10) — placeholders for any other name stay inert text. `allowlist`: the tenant HTTP allowlist;
    secrets go over plain http only to hosts on it (R11)."""
    if nonce is None:
        return url, headers, body, {}
    rx = placeholder_re(nonce, names or ())
    # The placeholder contains '#', which would confuse urlsplit: swap in delimiter-free index markers first.
    url_names: list[str] = []

    def mark(m):
        url_names.append(m.group(1))
        return f"\ue000{len(url_names) - 1}\ue001"

    # percent-encode our marker delimiters if the URL itself contains them, so data cannot forge a marker
    url = url.replace("\ue000", "%EE%80%80").replace("\ue001", "%EE%80%81")
    try:
        parts = urlsplit(rx.sub(mark, url))
    except ValueError:
        raise NodeError(_URL_ERR if url_names else "Invalid URL") from None
    if _MARK_RE.search(parts.scheme) or _MARK_RE.search(parts.netloc):
        raise NodeError(_URL_ERR)
    scheme, netloc, path, query = parts.scheme, parts.netloc, parts.path, parts.query  # fragment dropped

    # names actually used: path/query markers (a fragment-only placeholder is stripped, not used)
    names = []

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
    if any(rx.search(k) for k in headers):
        raise NodeError("secrets are not allowed in header names")
    if any(isinstance(k, str) and rx.search(k) for k in _body_keys(body)):
        raise NodeError("secrets are not allowed in body keys")
    if not names:
        return urlunsplit((scheme, netloc, path, query, "")), headers, body, {}
    if scheme.lower() not in ("http", "https") or not netloc:
        raise NodeError(_URL_ERR)
    if scheme.lower() == "http" and not host_on_allowlist(host, allowlist):
        raise NodeError(f"Secret {names[0]} can only be sent over HTTPS unless the host is on the tenant HTTP allowlist")

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
    try:
        final_host = urlsplit(urlunsplit((scheme, netloc, "/", "", ""))).hostname or ""
    except ValueError:
        raise NodeError("Invalid URL") from None
    if final_host.lower().rstrip(".") != (host or "").lower().rstrip("."):
        raise NodeError("secret host check mismatch")
    for n in names:
        if not host_allowed(final_host, rows[n].allowed_hosts):
            raise NodeError(f"Secret {n} is not allowed for host {final_host}")

    def sub(s, enc=lambda v: v):
        return rx.sub(lambda m: enc(used[m.group(1)]), s)

    new_headers = {}
    for k, v in headers.items():
        for n in rx.findall(v):
            problem = header_value_problem(used[n], n)
            if problem:
                raise NodeError(problem)
        new_headers[k] = sub(v)

    def usub(seg, enc):
        return _MARK_RE.sub(lambda m: enc(used[url_names[int(m.group(1))]]), seg)

    new_url = urlunsplit((scheme, netloc, usub(path, lambda v: quote(v, safe="")), usub(query, quote_plus), ""))
    new_body = _walk(body, sub)

    # Core UPDATE (not an ORM attribute change) so the updated_at onupdate does not fire on use
    await db.execute(update(TenantSecret).where(TenantSecret.id.in_([rows[n].id for n in used]))
                     .values(last_used_at=datetime.now(timezone.utc), updated_at=TenantSecret.updated_at)
                     .execution_options(synchronize_session=False))
    return new_url, new_headers, new_body, used


def _variants(used):
    out = set()
    for v in used.values():
        if v:
            out.update({v, quote(v, safe=""), quote(v), quote_plus(v), json.dumps(v)[1:-1]})
    return sorted(out, key=len, reverse=True)


def _mask(s, vs):
    for v in vs:
        s = s.replace(v, MASK)
    return s


# Limits: redaction is exact-substring only over the raw/URL/form/JSON-escaped variants. A value that a
# server echoes truncated, re-encoded (base64, hex, HTML entities...) or split is NOT masked.
def redact_text(s, used):
    return _mask(s, _variants(used))


def redact(obj, used):
    vs = _variants(used)
    if not vs:
        return obj
    values = {v for v in used.values() if v}

    def go(o):
        if isinstance(o, str):
            return _mask(o, vs)
        if isinstance(o, dict):
            return {(_mask(k, vs) if isinstance(k, str) else k): go(v) for k, v in o.items()}
        if isinstance(o, list):
            return [go(v) for v in o]
        if isinstance(o, tuple):
            return tuple(go(v) for v in o)
        if isinstance(o, (int, float)) and not isinstance(o, bool) and str(o) in values:
            return MASK
        return o
    return go(obj)
