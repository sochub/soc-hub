# Features

## Cases & investigation

Create and work security cases with severity/status, tags, a description, an
activity **timeline**, attached **artifacts** and **IOCs**, and an **audit trail**.
Cases gain a **Tasks** tab when a playbook is applied.

## Multi-tenancy

One account can belong to **many tenants** with a **different role per tenant**.

| Role | Scope | Can |
|---|---|---|
| `super_admin` | Global | Manage all tenants, switch into any, create admins, author marketplace playbooks |
| `admin` | Per tenant | Manage members, configure SSO, import/author playbooks |
| `analyst` | Per tenant | Create/update cases, artifacts, tasks |
| `viewer` | Per tenant | Read-only |

The active tenant rides in the JWT; switch it from the sidebar picker
(`POST /auth/switch-tenant`). All data is row-isolated by `tenant_id`.

## Investigation Copilot

A global, context-aware assistant powered by a **local** LLM (Ollama).

- **Auto-scoped** — on a case page it has the full case context; elsewhere it's a
  tenant-level assistant. Conversations persist per case and as one general session.
- **Actions (propose → confirm)** — when you ask it to *do* something it proposes a
  structured action (create case, add artifact, add timeline note, update case,
  find related cases). Nothing is written until you **Confirm**; a preview shows
  exactly what will be saved.
- **Deterministic notes** — note/comment text is parsed from your message (not
  hallucinated), so "add a comment: …" records exactly what you said.
- **Proactive suggestions** — it flags IOCs it notices in conversation
  ("add `1.2.3.4` as an artifact?") and offers to record findings to the timeline.

See the design notes: [global widget](copilot/A-global-widget-design.md),
[actions](copilot/B-actions-design.md).

## Playbooks (marketplace)

A global catalog of MITRE-mapped IR playbooks (Phishing, Ransomware, Malware,
Unauthorized Access, Data Exfiltration, Password Spraying). Tenants **import** the
ones they want and own editable copies. Applying a playbook to a case fills in
**phase-grouped tasks** (Identification → Containment → Eradication → Recovery →
Lessons Learned) with a progress tracker. Super-admins author new marketplace
templates; tenant admins author their own. See [playbooks](playbooks/A-playbooks-design.md).

## Telemetry dashboard

A light, developer-centric "Telemetry Console" dashboard:

- KPI tiles (total / open / critical / resolution rate / MTTR / this week) — the
  Total/Open/Critical tiles deep-link to a pre-filtered case list.
- Opened-vs-resolved 30-day trend, severity donut, **severity × status heatmap**,
  **open-case aging buckets**, status & IOC-type breakdowns, top shared indicators,
  recent-activity timeline, and priority queue.

## Investigation graph

An interactive force-directed map of **cases ◉, artifacts ▢, and IOCs ◇**. Dashed
**value-match bridges** connect an IOC to an artifact sharing the same value —
surfacing cross-case correlation at a glance. Filter by node type / severity /
threat, search, toggle "connected only", zoom, pan, drag nodes, and click any node
for a detail dossier with links into cases.

## Alert ingestion

External tools post alerts to `POST /api/v1/alerts/webhook` using a **per-tenant**
`X-API-Key`. The key determines the destination tenant; a leaked key can only ever
write to its owning tenant.

## Single Sign-On (SAML)

Tenant admins configure their own IdP (Okta, Entra ID, Google, …) under
**Settings → Single Sign-On**, with optional **JIT provisioning** (auto-create
users on first SSO login with a default role). Users sign in via "Sign in with SSO"
using their tenant slug. Password login remains available. See
[sso/saml-design.md](sso/saml-design.md).
