# Documentation

Documentation for **sochub — Case Management**. Start with the guides, then dive
into the per-subsystem design notes.

## Guides

| Guide | Description |
|---|---|
| [Getting Started](getting-started.md) | Run the stack, bootstrap an admin, seed demo data |
| [Architecture](architecture.md) | High-level components, data model, request flow |
| [Configuration](configuration.md) | Environment variables and security settings |
| [Configuration guide](configuration-guide.md) | Step-by-step SSO, MFA, guests, Slack and AI setup with screenshots, and what each error means |
| [Deployment](deployment.md) | Prebuilt images, Fargate, a VPS or Kubernetes |
| [Features](features.md) | Copilot, playbooks, dashboard, graph, multi-tenancy, webhooks |
| [Slack](slack/setup.md) | Bring-your-own Slack app, Socket Mode, `/sochub` |
| [Workflows](workflows/README.md) | Automation engine and vendor integrations |
| [Case guests](guests/README.md) | Inviting outside people to a single case |
| [Audit log](audit/README.md) | What the platform audit log records |
| [Sessions and OAuth](oauth/README.md) | Login sessions, refresh rotation, the OAuth authorization server, connected apps |
| [Connecting AI clients (MCP)](mcp/README.md) | MCP server for Claude Code, Claude Desktop and Cursor: setup, tools, roles, attribution, revoking |

## Design notes

These records capture the design decisions behind each subsystem (the "why", not
just the "what"). They double as a changelog of how the product was built.

| Area | Documents |
|---|---|
| **Copilot** | [Global widget](copilot/A-global-widget-design.md) · [Actions (propose→confirm)](copilot/B-actions-design.md) |
| **Playbooks** | [Design](playbooks/A-playbooks-design.md) · [Plan](playbooks/A-playbooks-plan.md) |
| **SSO** | [Per-tenant SAML](sso/saml-design.md) · [Login methods and OIDC](sso/oidc.md) |
| **Backlog** | [Index](backlog/README.md) — designed but unscheduled work |

## Conventions

- The backend API is versioned under `/api/v1`.
- All data endpoints are tenant-scoped; cross-tenant access returns `404`, not `403`.
- Database changes are Alembic migrations in `backend/alembic/versions/`.
