import asyncio
import json

import httpx
from sqlalchemy import select

from app.models.tenant import Tenant
from app.workflows.nodes import NodeContext, NodeError, RetryableNodeError, executor
from app.workflows.ssrf import SSRFError, assert_url_allowed

MAX_BODY = 1_000_000


@executor("http_request")
async def run_http(nctx: NodeContext, config: dict) -> dict:
    tenant = (await nctx.db.execute(select(Tenant).where(Tenant.id == nctx.run.tenant_id))).scalars().first()
    url = str(config["url"])
    try:
        # getaddrinfo blocks, so keep the guard off the event loop
        await asyncio.to_thread(assert_url_allowed, url, tenant.workflow_http_allowlist or [])
    except SSRFError as e:
        raise NodeError(str(e))

    method = str(config.get("method", "GET")).upper()
    headers = {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
    body = config.get("body")
    kwargs = {"json": body} if isinstance(body, (dict, list)) else ({"content": str(body)} if body not in (None, "") else {})
    async def _fetch():
        buf = bytearray()
        truncated = False
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            async with client.stream(method, url, headers=headers, **kwargs) as resp:
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BODY:
                        truncated = True
                        break
                return resp.status_code, dict(resp.headers), bytes(buf[:MAX_BODY]), truncated

    try:
        status, resp_headers, raw, truncated = await asyncio.wait_for(_fetch(), timeout=15)
    except asyncio.TimeoutError:
        raise RetryableNodeError(f"request to {url} timed out after 15s")
    except httpx.TransportError as e:
        raise RetryableNodeError(f"request failed: {e}")
    if status >= 500:
        raise RetryableNodeError(f"HTTP {status} from {url}")

    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw.decode(errors="replace")
    # 4xx is returned (not raised) so workflows can branch on output.status.
    out = {"status": status, "headers": resp_headers, "body": parsed}
    if truncated:
        out["truncated"] = True
    return out
