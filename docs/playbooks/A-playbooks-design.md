# Subsystem A — Playbook Templates (Marketplace) & Case Tasks

**Status:** ✅ Implemented 2026-06-09 (migration `f1a2b3c4d5e6`). Marketplace seeded with 6 templates. Part 1 of a 2-part effort (A = playbooks/tasks; B = AI triage & enrichment, builds on A). Reference for task content: https://github.com/austinsonger/Incident-Playbook (MITRE-mapped, before/during/after checklists).

## Decisions (locked)
| Fork | Decision |
|---|---|
| Sequence | Build A (playbooks/tasks) now; B (AI triage) is a separate later pass |
| Task model | **Phase-grouped** tasks — IR phases (PICERL): identification / containment / eradication / recovery / lessons_learned |
| Template distribution | **Marketplace**: a global read-only catalog; each tenant imports the templates it wants, then owns/modifies its copies (tenants start empty) |
| Case type | No new `case_type` enum — the applied playbook (`cases.playbook_template_id` + template category) gives a case its "type" |
| Scoping & perms | Tenant-scoped; read = any member, write templates / import = admin+, apply/edit case tasks = analyst+ |

## Data model (3 new tables + 1 column)

```
playbook_templates
  id, tenant_id (NULLABLE), name, category, description,
  is_system (bool), source_template_id (nullable), created_at
    -- tenant_id IS NULL  + is_system=true  -> MARKETPLACE template (read-only catalog)
    -- tenant_id = X       + is_system=false -> that tenant's own template
    -- source_template_id  -> the marketplace template it was imported from (nullable for hand-made)

playbook_task_templates              -- a template's task definitions
  id, template_id (FK playbook_templates, cascade), phase, title, description, order

case_tasks                           -- actual tasks on a case
  id, case_id (FK, cascade), tenant_id, phase, title, description,
  status ('todo'|'done'), "order", completed_at, completed_by (FK users), source_template_id

cases.playbook_template_id  (nullable FK playbook_templates)  -- which playbook this case runs
```

`phase` stored as a String with a fixed set: `identification | containment | eradication | recovery | lessons_learned`. UI groups tasks by phase in that order.

## API (`/playbooks` + case task routes)

**Marketplace & tenant templates**
- `GET /playbooks/marketplace` — list global catalog (tenant_id IS NULL). Any member.
- `POST /playbooks/import` — body `{ template_ids: [int] }`; deep-copies each marketplace template (+ its tasks) into the caller's tenant. Admin+. Skips ones already imported (by source_template_id) — returns how many imported/skipped.
- `GET /playbooks/` — list the tenant's own templates (tenant_id = active). Any member.
- `GET /playbooks/{id}` — template + its tasks (tenant-owned or marketplace). Any member.
- `POST /playbooks/` — create a tenant template from scratch; body includes `tasks: [{phase,title,description,order}]`. Admin+.
- `PUT /playbooks/{id}` — update a tenant-owned template (name/category/description + replace tasks). Admin+. (Marketplace templates are not editable.)
- `DELETE /playbooks/{id}` — delete a tenant-owned template. Admin+.

**Case tasks**
- `GET /cases/{case_id}/tasks` — tasks for the case (caller builds phase groups). Any member.
- `POST /cases/{case_id}/tasks` — add an ad-hoc task `{phase,title,description}`. Analyst+.
- `PUT /cases/{case_id}/tasks/{task_id}` — toggle status / edit title/description/phase. Analyst+.
- `DELETE /cases/{case_id}/tasks/{task_id}` — remove. Analyst+.
- `POST /cases/{case_id}/apply-playbook/{template_id}` — copy a tenant template's tasks onto the case (dedupe by title+phase), set `cases.playbook_template_id`. Analyst+.

## Frontend

- **Playbooks page** (new sidebar item, light console style) with two tabs:
  - **My Playbooks** — the tenant's templates; admins can create/edit (phase-grouped task editor) / delete; "Apply to case" not here (done from the case).
  - **Marketplace** — browse the global catalog (cards by category, task counts, phase preview); multi-select + **Import** (one or many). Already-imported ones show "Imported".
- **Case Detail → new "Tasks" tab**: phase-grouped checklist with a progress bar (X/Y done overall + per phase), check tasks done, add/edit/remove ad-hoc tasks, and an **"Apply playbook"** picker (lists the tenant's templates).
- **New Case modal**: optional **"Start from playbook"** dropdown (tenant templates) → after the case is created, the frontend calls apply-playbook to fill tasks.

## Seed
`seed_playbooks.py` — creates the **global marketplace catalog once** (tenant_id NULL, is_system=true), idempotent (skip if a system template with the same name exists). ~6 templates with phase-grouped tasks from the reference repo: Phishing (T1566), Malware Infection, Ransomware (T1486), Unauthorized/VPN Access (T1133), Data Exfiltration, Password Spraying (T1110).

## Migration
One Alembic revision (down_revision = current head `e1f2a3b4c5d6`): create `playbook_templates`, `playbook_task_templates`, `case_tasks`; add `cases.playbook_template_id`.

## Edge cases / rules
- Importing a marketplace template you already imported → skipped (idempotent), reported in the response.
- Editing/deleting a marketplace (system) template → 403; only tenant-owned templates are mutable.
- Deleting a tenant template does **not** remove tasks already applied to cases (they're independent `case_tasks` rows).
- Applying a playbook twice → dedupes by (phase, title) so it won't double-add.
- All template/task reads and writes scoped to the active tenant (marketplace is global-read only).

## Testing
- Unit-test the pure copy/dedupe logic: marketplace→tenant import (deep copy of tasks) and template→case-tasks apply (dedupe by phase+title).
- Build verification (tsc + vite); backend migration up/down.

## Out of scope (later)
- Subsystem B (AI triage that suggests/applies playbooks, sets severity/tags, suggests next steps).
- Task assignees / due dates / SLA timers (tasks are todo/done only for v1).
- Sharing a tenant's custom template back to the marketplace.
