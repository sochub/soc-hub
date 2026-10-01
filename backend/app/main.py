from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import app.db.base  # noqa: F401 — register all models
from app.api.api import api_router
from app.core.config import settings

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


app = FastAPI(
    title="SICMS API",
    description="Security Incident Case Management System API",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
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

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "Service is running"}
