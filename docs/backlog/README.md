# Backlog — future changes (not scheduled)

Designed and planned work that is **approved but deliberately not implemented yet**.
Each item has an approved design and a task-by-task implementation plan, ready to
pick up when prioritized.

| Item | Design | Plan | Notes |
|---|---|---|---|
| ✅ Multi-tenant memberships + tenant switching (DONE 2026-06-08) | [design](multi-tenant-memberships/design.md) | [plan](multi-tenant-memberships/plan.md) | Implemented (migration `d0e1f2a3b4c5`). One user in many tenants, per-tenant role, JWT active-tenant + sidebar switcher. |

## How to pick one up

The plan files are written for task-by-task execution. To implement one, drive it
with the `superpowers:subagent-driven-development` (recommended) or
`superpowers:executing-plans` skill, starting from Task 1.
