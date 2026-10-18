"""Records every write request and every 401/403 after the response is sent (pure ASGI: streaming-safe).

Never reads request bodies or query strings; stores the matched route template only. See docs/audit/README.md.
"""
import logging
import time
import uuid
from typing import Optional, Tuple

from starlette.requests import Request

from app.audit.context import AuditContext, request_id_var
from app.core.login_throttle import client_ip
from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXCLUDED_PREFIXES = ("/health", "/metrics", "/docs", "/redoc", "/api/v1/openapi.json", "/static")

# Write routes deliberately NOT recorded by the middleware. Every entry needs a reason; the coverage
# test fails if an entry names a route that doesn't exist.
AUDIT_EXCLUDED_ROUTES: dict = {}

# Routes whose entity isn't the innermost resource.
ENTITY_OVERRIDES = {"/api/v1/users/{user_id}/mfa/reset": "user_id"}


def derive_entity(route_path: Optional[str], path_params: dict) -> Tuple[Optional[str], Optional[int]]:
    if not route_path:
        return None, None
    parts = route_path.strip("/").split("/")
    names = [p[1:-1] for p in parts if p.startswith("{") and p.endswith("}")]
    wanted = ENTITY_OVERRIDES.get(route_path)
    for name in reversed(names):
        if wanted and name != wanted:
            continue
        if name != "id" and not name.endswith("_id"):
            continue
        try:
            value = int(str(path_params.get(name)))
        except (TypeError, ValueError):
            continue
        idx = parts.index("{" + name + "}")
        seg = parts[idx - 1] if idx > 0 else name
        return (seg[:-1] if seg.endswith("s") else seg), value
    return None, None


async def _insert(**fields) -> Optional[int]:
    async with AsyncSessionLocal() as db:
        row = AuditLog(**fields)
        db.add(row)
        await db.commit()
        return row.id


async def insert_row(**fields) -> Optional[int]:
    """Write one audit row in its own session. Never raises; returns the new id (None on failure)."""
    try:
        return await _insert(**fields)
    except Exception as exc:
        logger.error("audit insert failed request_id=%s err=%s", fields.get("request_id"), type(exc).__name__)
        return None


def _route_template(scope) -> Optional[str]:
    """Full matched route template. FastAPI 0.142 resolves included routers lazily, so scope["route"].path
    is relative to its router; the accumulated include prefix lives in FastAPI's scope state (pinned version)."""
    path = getattr(scope.get("route"), "path", None)
    if path is None:
        return None
    try:
        inc = scope.get("fastapi", {}).get("included_router")
        return inc.include_context.prefix + path if inc is not None else path
    except Exception:  # pragma: no cover - private API drift
        return path


def _outcome(code: int) -> str:
    if code < 400:
        return "success"
    if code in (401, 403):
        return "denied"
    return "error"


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] == "OPTIONS"
                or scope["path"].startswith(EXCLUDED_PREFIXES)):
            return await self.app(scope, receive, send)
        ctx = AuditContext()
        scope.setdefault("state", {})["audit"] = ctx
        rid = str(uuid.uuid4())
        token = request_id_var.set(rid)
        status = {"code": 500}
        started = time.monotonic()

        async def _send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            request_id_var.reset(token)
            await self._record(scope, ctx, rid, status["code"], int((time.monotonic() - started) * 1000))

    async def _record(self, scope, ctx: AuditContext, rid: str, code: int, ms: int) -> None:
        if ctx.skip:
            return
        method = scope["method"]
        route = _route_template(scope)
        if code in (401, 403):
            category, action = "security", "http.denied"
        elif method in WRITE_METHODS:
            if f"{method} {route}" in AUDIT_EXCLUDED_ROUTES:
                return
            category, action = "write", f"{method} {route or '(unmatched)'}"
        elif method in ("GET", "HEAD") and ctx.read_action and code < 400:
            category, action = "read", ctx.read_action
        else:
            return
        entity_type, entity_id = derive_entity(route, scope.get("path_params") or {})
        request = Request(scope)
        await insert_row(
            category=category, action=action, outcome=_outcome(code), status_code=code, method=method,
            route=route, entity_type=entity_type, entity_id=entity_id, tenant_id=ctx.tenant_id,
            user_id=ctx.user_id, user_email=ctx.user_email, actor=ctx.actor, ip=client_ip(request)[:45],
            user_agent=(request.headers.get("user-agent") or "")[:200] or None, request_id=rid, duration_ms=ms,
        )
