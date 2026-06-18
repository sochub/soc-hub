# In-app API Docs page

**Date:** 2026-06-18
**Status:** Approved

## Goal

Give integration builders an in-app page to read and use the SOC Hub API,
centered on the webhook ingestion flow, without hand-maintaining endpoint
documentation.

## Approach

Render the **live OpenAPI schema** FastAPI already generates (so the reference
never goes stale) via one embedded viewer component, with a short hand-written
webhook quickstart above it. A logged-in `/docs` route in the existing React app.

Decisions locked during brainstorming:
- **Scope:** full API reference (all endpoints), not a curated subset.
- **Delivery:** in-app React page (no static-site generator, no exposing
  FastAPI's built-in `/docs`+`/redoc`).
- **Access:** authenticated only (behind `ProtectedRoute`).
- **Content:** hand-written webhook quickstart + auto-generated full reference.

## Backend

### 1. Expose the OpenAPI schema through nginx
`backend/app/main.py`: pass `openapi_url="/api/v1/openapi.json"` to `FastAPI(...)`.
Today the schema sits at backend-root `/openapi.json`, which the nginx config
only proxies under `/api`, so it is unreachable same-origin. Moving it under
`/api/v1` makes the frontend able to fetch it. One line.

### 2. Declare `X-API-Key` as a security scheme
`backend/app/api/deps.py`: replace the bare `Header(..., alias="X-API-Key")` in
`get_webhook_from_key` with FastAPI's `APIKeyHeader(name="X-API-Key")` security
dependency. This makes the webhook ingest endpoint render in the reference with a
proper API-key auth block instead of a nondescript header parameter. Behavior is
unchanged (same header, same 401 on missing/invalid). The custom 401 for an
invalid key stays; `auto_error` is left at its default so a missing header still
yields 401/403 from the scheme — verify the missing-key path still returns 401
(adjust `auto_error=False` + explicit check if needed to preserve current
behavior, which `verify_webhooks.py` asserts).

## Frontend

### Dependency
One new dependency: an OpenAPI viewer. Primary choice **Scalar**
(`@scalar/api-reference`) — single component, modern light theme matching the
"Telemetry Console" look, built-in "try it". Fallback to **ReDoc** if React 19
peer dependencies conflict. Confirm the exact current package name and mount API
via Context7 at plan time before writing install steps.

### New page — `frontend/src/features/docs/ApiDocs.tsx`
- **Quickstart** (hand-written, `glass-panel`): how `X-API-Key` auth works, a link
  to the Integrations page to create a webhook key, the ingest request shape, and
  a working `curl` against `POST /api/v1/alerts/webhook`.
- **Full reference**: the viewer component pointed at `/api/v1/openapi.json`.

### Route + nav
- Route `/docs` inside `ProtectedRoute` in `frontend/src/App.tsx`.
- Sidebar item labelled "API Docs" in `frontend/src/components/layout/Layout.tsx`
  `baseSidebarItems` (lucide icon, e.g. `BookOpen` or `Code`).

## Testing / verification

- Frontend builds clean: `docker compose build frontend` (runs `tsc -b && vite build`).
- `/api/v1/openapi.json` returns 200 through nginx.
- The `/docs` page renders the quickstart and the embedded reference.
- The quickstart `curl` succeeds against the running backend (the underlying
  endpoint is already covered by `backend/verify_webhooks.py`).
- The webhook missing-key/invalid-key path still returns 401 after the
  `APIKeyHeader` change (re-run `verify_webhooks.py`).

## Out of scope

- Curating the reference to only integration-relevant endpoints (full reference
  chosen; can be revisited later).
- Public/unauthenticated docs.
- Separate static-site generator or external hosting.
- Per-endpoint hand-written prose beyond the webhook quickstart.
