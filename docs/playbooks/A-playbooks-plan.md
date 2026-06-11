# Playbooks & Case Tasks — Implementation Plan

> Build order for Subsystem A. See `A-playbooks-design.md` for the design.

**Backend**
1. Models — `models/playbook.py` (PlaybookTemplate, PlaybookTaskTemplate), `models/case_task.py` (CaseTask); add `Case.playbook_template_id`; register in `db/base.py`.
2. Migration `f1a2b3c4d5e6` (down_revision `e1f2a3b4c5d6`): 3 tables + `cases.playbook_template_id`.
3. Schemas — `schemas/playbook.py`, `schemas/case_task.py`.
4. Routes — `api/v1/playbooks.py` (marketplace/list/get/create/update/delete/import); `api/v1/case_tasks.py` (list/add/update/delete tasks, apply-playbook) mounted at `/cases`. Register both in `api/api.py`.
5. Seed — `scripts/seed_playbooks.py` global catalog (idempotent).

**Frontend**
6. Types — PlaybookTemplate, PlaybookTaskTemplate, CaseTask, IR_PHASES.
7. `api` helpers as needed (use existing axios client).
8. `features/playbooks/Playbooks.tsx` (My Playbooks + Marketplace tabs) + `TemplateEditorModal.tsx`.
9. Case Detail "Tasks" tab — `features/cases/CaseTasks.tsx` (phase-grouped checklist, progress, apply-playbook picker, ad-hoc add).
10. New Case modal — "Start from playbook" dropdown.
11. Route `/playbooks` in `App.tsx` + sidebar item in `Layout.tsx`.

**Deploy/verify** — migrate + restart backend; seed catalog; rebuild+redeploy frontend; smoke-test import + apply + toggle.
