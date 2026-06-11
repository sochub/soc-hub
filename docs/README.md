# Documentation

Documentation for **SOC Hub — Case Management**. Start with the guides, then dive
into the per-subsystem design notes.

## Guides

| Guide | Description |
|---|---|
| [Getting Started](getting-started.md) | Run the stack, bootstrap an admin, seed demo data |
| [Architecture](architecture.md) | High-level components, data model, request flow |
| [Configuration](configuration.md) | Environment variables and security settings |
| [Features](features.md) | Copilot, playbooks, dashboard, graph, multi-tenancy, webhooks |

## Design notes

These records capture the design decisions behind each subsystem (the "why", not
just the "what"). They double as a changelog of how the product was built.

| Area | Documents |
|---|---|
| **Copilot** | [Global widget](copilot/A-global-widget-design.md) · [Actions (propose→confirm)](copilot/B-actions-design.md) |
| **Playbooks** | [Design](playbooks/A-playbooks-design.md) · [Plan](playbooks/A-playbooks-plan.md) |
| **SSO** | [Per-tenant SAML](sso/saml-design.md) |
| **Backlog** | [Index](backlog/README.md) — designed but unscheduled work |

## Conventions

- The backend API is versioned under `/api/v1`.
- All data endpoints are tenant-scoped; cross-tenant access returns `404`, not `403`.
- Database changes are Alembic migrations in `backend/alembic/versions/`.
