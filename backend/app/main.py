from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import app.db.base  # noqa: F401 — register all models
from app.api import deps
from app.api.api import api_router
from app.audit.middleware import AuditMiddleware
from app.core.config import settings
from app.mcp.server import mcp_asgi, mcp_exact_path, mcp_server
from app.oauth.resources import RESOURCES, issuer
from app.oauth.routes import metadata as oauth_metadata

# Security headers applied to every response. These are cheap, defence-in-depth
# protections; the API serves JSON only, so a deny-all CSP is appropriate.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    # Browsers ignore HSTS over plain HTTP, so it is safe to always send.
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # A mounted Starlette app's own lifespan never runs: the host app must run the MCP session manager.
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(
    lifespan=lifespan,
    title="SICMS API",
    description="Security Incident Case Management System API",
    version="0.1.0",
    # No anonymous /docs, /redoc or schema: the frontend API Docs page fetches it signed in (below).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(SecurityHeadersMiddleware)

# CORS is opt-in: only enabled when explicit origins are configured. By default
# the frontend is served same-origin (nginx proxies /api), so no CORS is needed.
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Outermost app middleware: sees the final status of every request. See docs/audit/README.md.
app.add_middleware(AuditMiddleware)

app.include_router(api_router, prefix="/api/v1")


@app.get("/api/v1/openapi.json", include_in_schema=False, dependencies=[Depends(deps.get_current_active_user)])
async def openapi_schema():
    return app.openapi()


@app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def oauth_authorization_server_metadata():
    return oauth_metadata()


# RFC 9728 protected resource metadata. The MCP SDK names the root URL
# `<base>/.well-known/oauth-protected-resource/mcp` in its 401 WWW-Authenticate header, but its own route lives
# inside the /mcp mount, so the root documents are served here (nginx proxies /.well-known/oauth-protected-resource*).
def _mcp_resource_metadata() -> dict:
    res = RESOURCES["mcp"]
    return {"resource": res.url, "authorization_servers": [issuer()], "scopes_supported": list(res.scopes),
            "bearer_methods_supported": ["header"], "resource_name": "SOC Hub MCP"}


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
async def oauth_protected_resource_metadata():
    return _mcp_resource_metadata()


# MCP over Streamable HTTP (OAuth-protected inside the SDK app). See app/mcp/server.py.
app.add_route("/mcp", mcp_exact_path, include_in_schema=False)
app.mount("/mcp", mcp_asgi)


@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Service is running"}
