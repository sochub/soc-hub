import asyncio
import json
import logging
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from app.models.tenant import Tenant
from app.secrets.refs import placeholder_re
from app.secrets.resolve import redact, redact_text, resolve_for_request
from app.workflows.nodes import NodeContext, NodeError, RetryableNodeError, executor
from app.workflows.ssrf import SSRFError, assert_url_allowed

MAX_BODY = 1_000_000
logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO ("HTTP Request: GET https://...?key=<secret>"): never let that through.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def _strings(o):
    """All strings in o, including dict keys."""
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _strings(v)


def _log_secret_use(tenant_id, names, host, outcome):
    # names/ids/hosts only, never values
    logger.info("secret use tenant=%s names=%s host=%s outcome=%s", tenant_id, sorted(names), host, outcome)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- names/hosts only, never values


@executor("http_request")
async def run_http(nctx: NodeContext, config: dict) -> dict:
    tenant = (await nctx.db.execute(select(Tenant).where(Tenant.id == nctx.run.tenant_id))).scalars().first()
    url = str(config["url"])
    try:
        # getaddrinfo blocks, so keep the guard off the event loop
        pinned_ip = await asyncio.to_thread(assert_url_allowed, url, tenant.workflow_http_allowlist or [])
    except SSRFError as e:
        raise NodeError(str(e))

    method = str(config.get("method", "GET")).upper()
    headers = {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
    body = config.get("body")

    # Secrets are substituted only now, after the SSRF check (a secret can never be in the host).
    used: dict = {}
    host = ""
    nonce = nctx.secret_nonce
    issued = set(nctx.secret_names or ())
    rx = placeholder_re(nonce, issued) if nonce else None
    if rx and any(rx.search(x) for x in [url, *headers.keys(), *headers.values(), *_strings(body)]):
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            raise NodeError("invalid URL") from None
        try:
            url, headers, body, used = await resolve_for_request(
                nctx.db, nctx.run.tenant_id, url, headers, body, host, nonce, issued,
                tenant.workflow_http_allowlist or [])
        except NodeError:
            names = {n for x in [url, *headers.keys(), *headers.values(), *_strings(body)] for n in rx.findall(x)}
            _log_secret_use(nctx.run.tenant_id, names, host, "blocked")
            raise
    outcome = "error"  # also for CancelledError/BaseException: only a completed request is "ok"
    try:
        out = redact(await _send(url, method, headers, body, pinned_ip, bool(used)), used)
        outcome = "ok"
        return out
    except NodeError as e:
        raise type(e)(redact_text(str(e), used)) from None
    except Exception as e:  # unexpected library error after substitution: never echo its text
        if not used:
            raise
        raise NodeError(f"request failed: {type(e).__name__}") from None
    finally:
        if used:
            _log_secret_use(nctx.run.tenant_id, used, host, outcome)


async def _send(url, method, headers, body, pinned_ip, secret=False) -> dict:
    # target / Host / SNI are all built from the URL after substitution
    target, extensions = url, {}
    try:
        parsed = httpx.URL(url)
        if pinned_ip:
            # Connect to the vetted IP (no second DNS lookup to rebind), but keep the hostname for the
            # Host header and for TLS: httpcore uses `sni_hostname` as server_hostname, so SNI and
            # certificate verification are still against the original name.
            target = parsed.copy_with(host=pinned_ip)
    except httpx.InvalidURL as e:
        raise NodeError("invalid URL" if secret else f"invalid URL: {e}")
    if pinned_ip:
        headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        headers["Host"] = parsed.netloc.decode("ascii")
        extensions = {"sni_hostname": parsed.host}
    kwargs = {"json": body} if isinstance(body, (dict, list)) else ({"content": str(body)} if body not in (None, "") else {})

    async def _fetch():
        buf = bytearray()
        truncated = False
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            async with client.stream(method, target, headers=headers, extensions=extensions, **kwargs) as resp:
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BODY:
                        truncated = True
                        break
                return resp.status_code, dict(resp.headers), bytes(buf[:MAX_BODY]), truncated

    try:
        status, resp_headers, raw, truncated = await asyncio.wait_for(_fetch(), timeout=15)
    except asyncio.TimeoutError:
        raise RetryableNodeError(f"request to {'<redacted url>' if secret else url} timed out after 15s")
    except httpx.InvalidURL as e:
        raise NodeError("invalid URL" if secret else f"invalid URL: {e}")
    except httpx.TransportError as e:
        raise RetryableNodeError(f"request failed: {type(e).__name__ if secret else e}")
    except httpx.HTTPError as e:
        raise NodeError(f"request failed: {type(e).__name__ if secret else e}")
    if status >= 500:
        raise RetryableNodeError(f"HTTP {status} from {'<redacted url>' if secret else url}")

    try:
        parsed_body = json.loads(raw)
    except ValueError:
        parsed_body = raw.decode(errors="replace")
    # 4xx is returned (not raised) so workflows can branch on output.status.
    out = {"status": status, "headers": resp_headers, "body": parsed_body}
    if truncated:
        out["truncated"] = True
    return out
