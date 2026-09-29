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
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            resp = await client.request(method, url, headers=headers, **kwargs)
    except httpx.TransportError as e:
        raise RetryableNodeError(f"request failed: {e}")
    if resp.status_code >= 500:
        raise RetryableNodeError(f"HTTP {resp.status_code} from {url}")

    raw = resp.content[:MAX_BODY]
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw.decode(errors="replace")
    # 4xx is returned (not raised) so workflows can branch on output.status.
    return {"status": resp.status_code, "headers": dict(resp.headers), "body": parsed}
