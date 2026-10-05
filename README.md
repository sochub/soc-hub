<div align="center">

# SOC Hub — Case Management

**A multi-tenant Security Operations Center (SOC) case-management platform with a built-in, local-LLM investigation copilot.**

FastAPI · React 19 · PostgreSQL · Ollama

<br/>

<img src="docs/images/dashboard.png" alt="SOC Hub operations dashboard" width="900" />

</div>

---

SOC Hub is an incident-response workbench for security teams. Analysts triage cases,
attach IOCs and artifacts, work IR playbooks, and visualize how indicators connect
cases — assisted by an AI copilot that runs entirely on a **local** model (no data
leaves your infrastructure) and proposes actions you explicitly approve.

## Highlights

- 🏢 **Multi-tenant**: one account, many tenants, and a **role per tenant**. Data is isolated per row, and there's an in-app tenant switcher.
- 🤖 **Investigation Copilot**: a global, context-aware assistant.
  - It runs on a local LLM (Ollama) by default, or on the AI provider you configure per tenant.
  - It **proposes actions** that you confirm before anything is written.
- 🧠 **AI triage**: every new case gets a suggested severity, tags, playbook, related cases and next steps.
- 📋 **Playbooks**: a **marketplace** of MITRE-mapped IR playbooks.
  - Applying one fills the case with phase-grouped task checklists.
- 🧪 **Threat-intel enrichment**: IOCs and artifacts are checked against VirusTotal, URLhaus, ThreatFox, RDAP and crt.sh.
  - Enrichment respects TLP, and private values never leave your network.
- 📎 **Evidence**: file attachments on cases.
  - Malware samples are stored in a password-protected ZIP, and each upload is hashed into an artifact.
- 📝 **Incident reports**: an AI-assisted draft plus analyst editing.
  - Includes TTD/TTC/TTR and swimlane timeline charts.
  - Exports to PDF or JSON with TLP marking.
- ⚙️ **Automations**: a visual workflow builder with conditions, loops, case and alert actions, and HTTP requests.
  - Includes Slack messages and "ask a person" buttons, plus dry runs.
- 🔑 **Workflow secrets**: encrypted credentials bound to allowed hosts.
  - They're inserted only at send time and redacted from run history.
- 🔔 **Collaboration**: `@mentions` in comments, case following, and an in-app notification bell.
  - Mentions and assignments also go out by Slack DM or email.
- 👤 **Profiles & two-factor**: photo, timezone, and a password change that signs out your other sessions.
  - Authenticator-app MFA, which a tenant can make mandatory.
- 📊 **Telemetry dashboard**: opened-vs-resolved trends, a severity × status heatmap, case aging, MTTR and SLA compliance.
- 🕸️ **Investigation graph**: a force-directed map of cases, artifacts and IOCs that reveals cross-case correlation.
- 🔐 **Security first**:
  - per-tenant webhook keys and per-tenant SAML SSO;
  - Argon2 password hashing and session revocation;
  - SSRF-guarded automations;
  - security headers and full audit logging.

## Screenshots

### 🗂️ Cases: AI triage, timeline and @mentions

Each case opens with its AI triage, its SLA status and a **Follow** toggle.
In comments you can mention a teammate by typing `@`.

<img src="docs/images/case-overview.png" alt="Case overview with AI triage and follow" width="900" />

<table>
<tr>
<td width="50%"><img src="docs/images/case-timeline.png" alt="Case timeline with a highlighted @mention" /></td>
<td width="50%"><img src="docs/images/mentions.png" alt="@mention autocomplete in a comment" /></td>
</tr>
</table>

### 🔔 Notifications

Mentions, assignments, status and severity changes, comments on cases you follow,
and SLA breaches all land in the bell. Mentions and assignments also go out by
Slack DM or email.

<img src="docs/images/notifications.png" alt="Notification panel" width="900" />

### 📝 Incident reports

The report tab lets you write an AI-assisted draft and edit it. It includes a
detection → containment → recovery bar and a swimlane timeline. You can export it
as a TLP-marked PDF or as JSON.

<img src="docs/images/case-report.png" alt="Incident report with timeline charts" width="900" />

### 🕸️ Investigation graph: see how indicators connect cases

<img src="docs/images/investigation-graph.png" alt="Investigation graph" width="900" />

### ⚙️ Automations

A visual workflow editor with typed node inspectors, dry runs and run history.
Steps include Slack "ask a person" prompts, HTTP calls and case actions.

<img src="docs/images/automations-editor.png" alt="Workflow editor" width="900" />

### 🤖 AI copilot: you confirm every proposed action

<table>
<tr>
<td width="50%"><img src="docs/images/copilot-case.png" alt="Copilot on a case" /></td>
<td width="50%"><img src="docs/images/copilot-action-card.png" alt="Copilot action card with confirm/cancel" /></td>
</tr>
</table>

### 📋 Playbooks & tasks

<table>
<tr>
<td width="50%"><img src="docs/images/playbooks-marketplace.png" alt="Playbook marketplace" /></td>
<td width="50%"><img src="docs/images/case-tasks.png" alt="Phase-grouped case tasks" /></td>
</tr>
</table>

### 🗃️ Cases, IOCs & artifacts

<table>
<tr>
<td width="50%"><img src="docs/images/cases-list.png" alt="Cases list" /></td>
<td width="50%"><img src="docs/images/iocs.png" alt="Indicators of compromise" /></td>
</tr>
</table>

### 👤 Profile & two-factor authentication

<table>
<tr>
<td width="50%"><img src="docs/images/profile.png" alt="Profile page" /></td>
<td width="50%"><img src="docs/images/profile-security.png" alt="Password change and two-factor" /></td>
</tr>
<tr>
<td width="50%"><img src="docs/images/users-mfa.png" alt="Users with MFA status" /></td>
<td width="50%"><img src="docs/images/settings-security.png" alt="Require two-factor for the tenant" /></td>
</tr>
</table>

<details>
<summary><b>More screenshots</b></summary>

<br/>

**Integrations: AI provider and threat intelligence**

<img src="docs/images/integrations.png" alt="Integrations: AI provider and threat intel" width="820" />

**Workflow secrets**

<img src="docs/images/secrets.png" alt="Workflow secrets" width="820" />

**Artifact repository: indicators shared across cases**

<img src="docs/images/artifacts.png" alt="Artifact repository" width="820" />

**Copilot: general (queue-level) assistant**

<img src="docs/images/copilot-general.png" alt="General copilot" width="420" />

</details>

## Tech stack

| Layer | Technology |
|---|---|
| Backend | FastAPI · SQLAlchemy (async) · PostgreSQL (asyncpg) · Alembic |
| Frontend | React 19 · TypeScript · TanStack Query · React Router v7 · Tailwind CSS · Recharts |
| Auth | JWT (HS256) with session revocation · Argon2 · TOTP two-factor · per-tenant SAML SSO (python3-saml) |
| Background | Celery · Redis |
| AI | Ollama (local, default `llama3`) or a per-tenant provider |
| Infra | Docker Compose |

## Quick start

```bash
# 1. Bring up the full stack (db, redis, ollama, backend, worker, frontend)
docker compose up -d --build      # the ollama container auto-pulls the model on first boot

# 2. Apply database migrations
docker compose exec backend alembic upgrade head

# 3. Create the first super-admin (prompts securely for a password)
docker compose exec backend python -m app.scripts.create_super_admin \
  --email admin@example.com --name "Super Admin"
```

Then open **http://localhost** and sign in. For the full walkthrough (seeding demo
data, configuring SSO, etc.) see **[docs/getting-started.md](docs/getting-started.md)**.

> **Heads-up:** copy `.env.example` → `.env` and `backend/.env.example` →
> `backend/.env`. Before exposing this anywhere, set `ENVIRONMENT=production` and
> strong `SECRET_KEY`, `POSTGRES_PASSWORD` and `REDIS_PASSWORD` values: in
> production the app refuses to boot with weak or default credentials. The
> defaults are for **local development only**.

## Documentation

Full docs live in **[`docs/`](docs/README.md)**:

| Doc | What's in it |
|---|---|
| [Getting Started](docs/getting-started.md) | Setup, bootstrap, demo data, model pull |
| [Architecture](docs/architecture.md) | Components, data model, request flow |
| [Configuration](docs/configuration.md) | Environment variables, security knobs |
| [Features](docs/features.md) | Copilot, playbooks, dashboard, graph, multi-tenancy |
| [SAML SSO](docs/sso/saml-design.md) | Per-tenant single sign-on |
| [Design notes](docs/README.md#design-notes) | Per-subsystem design records |

## Status & license

Active development. This is a portfolio/reference implementation — review the
security posture and configuration before any production use. License: _to be
decided by the repository owner._
