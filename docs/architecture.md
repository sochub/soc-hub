# Architecture

## Overview

```
                       ┌──────────────┐
                       │   Browser    │
                       └──────┬───────┘
                              │  :80
                       ┌──────▼───────┐      proxies /api/v1 → backend:8000
                       │  frontend    │  (nginx, built React 19 SPA)
                       └──────┬───────┘
                              │
                       ┌──────▼───────┐        ┌───────────┐
                       │   backend    │◀──────▶│  ollama   │  local LLM
                       │  (FastAPI)   │        └───────────┘
                       └──┬────────┬──┘
                ┌─────────▼──┐   ┌─▼──────────┐   ┌──────────┐
                │ PostgreSQL │   │   Redis    │◀──│  worker  │ (Celery)
                └────────────┘   └────────────┘   └──────────┘
```

## Backend (`backend/app`)

| Module | Responsibility |
|---|---|
| `api/v1/` | Route handlers, one file per resource; registered in `api/api.py` |
| `api/deps.py` | Auth dependencies: current user, active-tenant resolution, role checks |
| `models/` | SQLAlchemy ORM models |
| `schemas/` | Pydantic request/response models |
| `services/` | AI (Ollama), SAML, email, Jira integrations |
| `utils/` | Audit logging, role resolution, copilot heuristics |
| `scripts/` | CLI: create super-admin, seed incidents, seed playbooks |
| `alembic/` | Database migrations |

## Data model (core)

- **users** — account + `is_super_admin` flag (no per-tenant role here).
- **tenants** — each with its own `webhook_api_key`.
- **tenant_memberships** `(user_id, tenant_id, role)` — the single source of truth
  for a user's role *in a tenant* (`admin` / `analyst` / `viewer`).
- **tenant_sso_configs** — per-tenant SAML settings (1:1 with tenant).
- **cases** → **timeline_events**, **case_artifacts** (↔ **artifacts**), **iocs**,
  **case_tasks**, optional `playbook_template_id`.
- **playbook_templates** + **playbook_task_templates** — `tenant_id IS NULL` rows
  are the global marketplace; tenant copies reference `source_template_id`.
- **copilot_sessions** → **copilot_messages** (with optional `action` and
  `suggestions`). A `NULL` `case_id` session is the user's general assistant.
- **audit_logs** — every mutation, tenant-scoped.

## Auth & multi-tenancy

- Login issues a JWT carrying `sub` (email) and `active_tenant_id`.
- `deps.get_effective_tenant_id()` resolves the active tenant from the token and
  validates membership; role checks resolve the role from the active membership.
- Switching tenants (`POST /auth/switch-tenant`) re-issues the token.
- SAML SSO posts to `/auth/saml/{slug}/acs`, then redirects to the SPA with the
  JWT in the URL fragment.

Every data query filters by `tenant_id`; cross-tenant access returns `404`.

## Frontend (`frontend/src`)

- `features/` — one folder per area (cases, artifacts, iocs, playbooks, copilot,
  tenants, settings, admin, superadmin).
- `components/layout/Layout.tsx` — app shell (collapsible sidebar, tenant switcher,
  global copilot widget).
- TanStack Query for server state; axios client in `api/client.ts` attaches the JWT
  and handles 401 → re-login.
- Theme: light "Telemetry Console" — see [features.md](features.md).
