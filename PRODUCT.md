# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Primary: solo responders and tiny incident-response teams (one or two people wearing every hat). They triage alerts coming from tools they already run (SIEM, EDR, email security), open and work cases, and write the report, usually under time pressure and without a dedicated SOC platform team to set things up. Speed to useful and low setup cost matter more than enterprise depth.

Secondary (optional capability): people who run several organizations or keep departments' incidents apart, switching between them.

## Product Purpose
sochub is case management for security incidents: the place where a team handles an incident from first alert to closed case. Alerts arrive by webhook or scheduled pull workflow; analysts triage them into cases, attach IOCs, artifacts and evidence, follow IR playbooks, run response workflows, and produce the incident report. Success is an incident worked end to end in one place, with a clear record of what happened and what was done.

## Positioning
- Case management, **not a SIEM** and not a SIEM replacement: no log collection, event search or detection rules.
- Open-source (MIT/Apache-2.0), self-hosted.
- AI copilot and triage run on a **local** LLM (Ollama) by default, so incident data never leaves the user's infrastructure; per-organization AI providers are optional.
- The copilot **proposes** actions; nothing is written until a person confirms.

## Operating Context
- Self-hosted via Docker Compose or Helm; FastAPI + PostgreSQL backend, React web app.
- Inputs: alerts from the user's existing security tools; threat-intel lookups (VirusTotal, URLhaus, ThreatFox, RDAP, crt.sh) gated by TLP.
- Outputs: case records, incident reports (PDF/JSON with TLP marking), Slack/email notifications, tickets in Jira/HaloPSA/ConnectWise.
- AI clients (Claude Code, Claude Desktop, Cursor) can connect over MCP.

## Capabilities and Constraints
- Core: alerts triage and suppression, cases (Kanban, My Queue, ⌘K), per-case Activity feed, artifacts & assets, investigation graph, playbook marketplace with phase-grouped tasks, workflows builder with vendor templates, workflow secrets, incident reports, telemetry dashboard, collaboration (mentions, following, notifications), MFA/SSO/OIDC, case guests, audit log.
- Multiple organizations are optional; a single team never sees that machinery.
- Terminology: say "organization", never "tenant", "client", "MSSP" or "Portfolio" in user-facing copy.
- Never name competitor products in the repo, commits, PRs or docs.
- Undecided: any hosted or commercial offering.

## Brand Commitments
- Name: sochub (lowercase in copy). Marketing site: sochub.io; the app's look follows it.
- Voice: plain, factual, specific about what the product does and does not do (see README "not a SIEM" framing).

## Evidence on Hand
- Product screenshots: `docs/images/` (dashboard, cases board, queue, etc.); product tour video: `docs/media/tour.mp4`.
- No customer testimonials, logos, benchmarks or usage numbers exist; do not invent them.

## Product Principles
1. Lean for the one-person team: useful in minutes, no admin overhead before the first case.
2. Data stays home: local-first AI and TLP-respecting enrichment are defaults, not upsells.
3. Humans approve: automation and AI propose; people confirm anything that writes or acts.
4. One story per incident: alert, evidence, actions and report live in the same case record.
5. Honest scope: complement the detection stack, never pretend to be it.

## Accessibility & Inclusion
No formal standard committed yet (open decision). Recommended baseline: WCAG 2.2 AA, i.e. sufficient contrast on the dark theme, full keyboard operation, visible focus, and screen-reader labels.
