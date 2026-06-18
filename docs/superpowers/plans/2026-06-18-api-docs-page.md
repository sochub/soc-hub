# In-app API Docs Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a logged-in `/docs` page that shows a hand-written webhook quickstart plus the auto-generated full API reference rendered from the live OpenAPI schema.

**Architecture:** Expose FastAPI's OpenAPI JSON under the nginx-proxied `/api/v1` path, declare the webhook `X-API-Key` as a security scheme so it renders as auth, then embed the Scalar API-reference React component pointed at that schema in a new React route, with a hand-written quickstart above it.

**Tech Stack:** FastAPI (OpenAPI), React 19.2 + Vite 7 + React Router 7 + TanStack Query, `@scalar/api-reference-react`.

## Global Constraints

- Branch: `feat/alerts-ui-multi-webhook` (this work stacks on the alerts/webhook feature). Stay on it.
- Backend in `backend/`, Docker. After backend edits: `docker restart case_management-backend-1` (note: container name uses dashes; virtiofs breaks `--reload`).
- Frontend is an nginx image; deps install via the Dockerfile's `npm install`. Host has node v26 / npm 11. To see changes: `docker compose build frontend && docker compose up -d frontend`. The build runs `tsc -b && vite build`, so it type-checks.
- nginx proxies only `/api/v1/` to the backend (`frontend/nginx.conf`); anything the browser fetches must be under `/api/v1/` or a static asset.
- The webhook ingest endpoint is `POST /api/v1/alerts/webhook`, authed by header `X-API-Key`, body `{external_id, title, payload}`. Its behavior is asserted by `backend/verify_webhooks.py` (must keep passing).
- Theme: light "Telemetry Console" — `glass-panel` panels, `accent-600`/`accent-700`, zinc palette, IBM Plex Sans / Roboto Mono.

---

### Task 1: Expose OpenAPI schema + declare X-API-Key security scheme

**Files:**
- Modify: `backend/app/main.py:30-34` (FastAPI constructor — add `openapi_url`)
- Modify: `backend/app/api/deps.py` (`get_webhook_from_key` — use `APIKeyHeader`)

**Interfaces:**
- Produces: a reachable `GET /api/v1/openapi.json`; the `/alerts/webhook` operation in that schema carries an `APIKeyHeader` security requirement named `WebhookApiKey`.

- [ ] **Step 1: Point the OpenAPI URL under the proxied path**

In `backend/app/main.py`, change the `FastAPI(...)` constructor (currently lines 30-34) to:
```python
app = FastAPI(
    title="SICMS API",
    description="Security Incident Case Management System API",
    version="0.1.0",
    openapi_url="/api/v1/openapi.json",
)
```

- [ ] **Step 2: Switch the webhook key dependency to APIKeyHeader**

In `backend/app/api/deps.py`, add the import (near the other FastAPI imports at the top):
```python
from fastapi import Security
from fastapi.security import APIKeyHeader
```
Add this module-level scheme (place it above `get_webhook_from_key`):
```python
# Named so the OpenAPI reference shows the webhook auth as an API key in the
# X-API-Key header. auto_error=False keeps our own 401 below (a missing header
# returns 401, matching the invalid-key behavior verify_webhooks.py asserts).
webhook_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="WebhookApiKey")
```
Then change the signature of `get_webhook_from_key` from:
```python
async def get_webhook_from_key(
    db: AsyncSession = Depends(get_db),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> Webhook:
```
to:
```python
async def get_webhook_from_key(
    db: AsyncSession = Depends(get_db),
    x_api_key: str | None = Security(webhook_api_key_scheme),
) -> Webhook:
```
Add a guard as the first statement in the body (before the DB query), so a missing header is rejected the same way an invalid one is:
```python
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
```
Leave the rest of the function (the `select`, the `is_active` join, the final 401) unchanged. `Header` may now be unused in this function — only remove the `Header` import if nothing else in `deps.py` uses it (grep first: `grep -n "Header" backend/app/api/deps.py`).

- [ ] **Step 3: Restart the backend and verify clean startup**

Run: `docker restart case_management-backend-1 && sleep 5 && docker logs --tail 20 case_management-backend-1`
Expected: ends with `Application startup complete.`, no ImportError/traceback.

- [ ] **Step 4: Verify the schema is reachable through nginx and carries the security scheme**

Run: `curl -s http://localhost/api/v1/openapi.json | python3 -c "import sys,json; d=json.load(sys.stdin); print('paths ok:', '/api/v1/alerts/webhook' in d['paths']); print('scheme:', list(d.get('components',{}).get('securitySchemes',{}).keys()))"`
Expected: `paths ok: True` and `scheme: ['WebhookApiKey']` (the scheme name appears).

- [ ] **Step 5: Verify webhook behavior is unchanged**

Run: `docker compose exec backend python verify_webhooks.py`
Expected: ends with `ALL CHECKS PASSED` (bad/revoked key still 401, ingest still stamps source).

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/api/deps.py
git commit -m "feat(api): expose openapi.json under /api/v1 and declare webhook API key scheme"
```

---

### Task 2: API Docs page (Scalar reference + webhook quickstart) + route + nav

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json` (add `@scalar/api-reference-react`)
- Create: `frontend/src/features/docs/ApiDocs.tsx`
- Modify: `frontend/src/App.tsx` (import + route)
- Modify: `frontend/src/components/layout/Layout.tsx` (sidebar item)

**Interfaces:**
- Consumes: `GET /api/v1/openapi.json` (Task 1).
- Produces: default-exported `ApiDocs` component at route `/docs`.

- [ ] **Step 1: Add the Scalar dependency**

Run (on the host, updates package.json + lockfile):
```bash
cd frontend && npm install @scalar/api-reference-react
```
Expected: installs without peer-dependency errors (the package supports React 19). If npm reports a React 19 peer conflict, retry with `npm install @scalar/api-reference-react --legacy-peer-deps` and note it in the report.

- [ ] **Step 2: Create the ApiDocs page**

`frontend/src/features/docs/ApiDocs.tsx`:
```tsx
import { ApiReferenceReact } from '@scalar/api-reference-react';
import '@scalar/api-reference-react/style.css';
import { Link } from 'react-router-dom';

const ingestUrl = `${window.location.origin}/api/v1/alerts/webhook`;
const curlSample = `curl -X POST ${ingestUrl} \\
  -H "X-API-Key: <your webhook key>" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"siem-123","title":"Suspicious login","payload":{"ip":"1.2.3.4"}}'`;

export default function ApiDocs() {
    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold text-zinc-900 tracking-tight">API Docs</h1>
                <p className="text-zinc-500 mt-1">Build integrations against the SOC Hub API.</p>
            </div>

            <div className="glass-panel p-5 rounded-xl border border-zinc-200 space-y-3">
                <h2 className="font-semibold text-zinc-800">Webhook quickstart</h2>
                <p className="text-sm text-zinc-600">
                    Push alerts from a SIEM or automation into a tenant in three steps:
                </p>
                <ol className="text-sm text-zinc-600 list-decimal list-inside space-y-1">
                    <li>
                        Create a webhook on the{' '}
                        <Link to="/integrations" className="text-accent-600 hover:underline">Integrations</Link>{' '}
                        page (admin only). Each webhook has its own <span className="font-mono">X-API-Key</span>;
                        the alert's <span className="font-mono">source</span> is set from the webhook's name.
                    </li>
                    <li>
                        Send a <span className="font-mono">POST</span> to{' '}
                        <span className="font-mono">/api/v1/alerts/webhook</span> with the{' '}
                        <span className="font-mono">X-API-Key</span> header. The key alone determines the
                        destination tenant — there is no tenant field in the request.
                    </li>
                    <li>
                        The new alert appears in the{' '}
                        <Link to="/alerts" className="text-accent-600 hover:underline">Alerts</Link>{' '}
                        queue, where an analyst can promote it to a case or dismiss it.
                    </li>
                </ol>
                <pre className="text-xs text-zinc-600 bg-zinc-50 rounded-lg p-3 overflow-x-auto">{curlSample}</pre>
            </div>

            <div className="glass-panel rounded-xl border border-zinc-200 overflow-hidden">
                <ApiReferenceReact configuration={{ url: '/api/v1/openapi.json', theme: 'default' }} />
            </div>
        </div>
    );
}
```

- [ ] **Step 3: Add the route**

In `frontend/src/App.tsx`, add the import alongside the other feature imports:
```tsx
import ApiDocs from './features/docs/ApiDocs';
```
And add the route inside the `<Route path="/" element={<Layout />}>` block (e.g. right after the `settings` route):
```tsx
<Route path="docs" element={<ApiDocs />} />
```

- [ ] **Step 4: Add the sidebar nav item**

In `frontend/src/components/layout/Layout.tsx`, add `Code` to the existing `lucide-react` import line, then add this entry to the `baseSidebarItems` array (after the Integrations entry):
```tsx
    { icon: Code, label: 'API Docs', path: '/docs' },
```

- [ ] **Step 5: Build (type-check) the frontend**

Run: `docker compose build frontend 2>&1 | tail -5`
Expected: completes with `Built` / no TypeScript errors. If `tsc` errors on the Scalar import types, ensure the package and its `style.css` import path match what was installed (check `frontend/node_modules/@scalar/api-reference-react/package.json` `exports`); do not silence with `// @ts-ignore` unless the package genuinely ships no types, and note it in the report if so.

- [ ] **Step 6: Deploy and verify the container is up**

Run: `docker compose up -d frontend && sleep 2 && docker compose ps frontend`
Expected: `case_management-frontend-1` shows `Up`.

- [ ] **Step 7: Verify the page serves and the schema loads**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost/docs && curl -s -o /dev/null -w "%{http_code}\n" http://localhost/api/v1/openapi.json`
Expected: `200` then `200` (the SPA shell serves at `/docs`; the schema the page fetches returns 200).

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/features/docs/ApiDocs.tsx frontend/src/App.tsx frontend/src/components/layout/Layout.tsx
git commit -m "feat(frontend): in-app API docs page with webhook quickstart + OpenAPI reference"
```

---

## Self-Review Notes

- **Spec coverage:** expose openapi under `/api/v1` (T1.1); `X-API-Key` security scheme + preserved 401 (T1.2, verified T1.5); Scalar dependency (T2.1); quickstart + full reference page (T2.2); `/docs` route behind ProtectedRoute (T2.3 — the route nests inside the existing `ProtectedRoute`→`Layout` block); sidebar nav (T2.4); build/serve verification (T2.5–T2.7). All spec sections mapped.
- **Type consistency:** `ApiReferenceReact` configuration prop uses `{ url, theme }` per the confirmed Scalar API; page is default-exported and imported as `ApiDocs` in App.tsx. Route path `docs` matches the curl check `/docs`.
- **Known risk flagged inline:** React 19 peer-dep on the Scalar install (T2.1) and possible type/exports mismatch (T2.5) — both have inline fallbacks.
