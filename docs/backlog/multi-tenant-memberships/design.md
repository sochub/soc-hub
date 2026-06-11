# Design: Multi-tenant memberships + tenant switching

**Status:** ✅ Implemented 2026-06-08 (migration `d0e1f2a3b4c5`). Implementation deviated from this design in one respect: membership `role` is stored as a plain `String` (not a `TenantRole` enum) to avoid the legacy `userrole` Postgres enum type, and `/users/me` returns the active-tenant role as `role` so existing frontend role checks keep working. Tenant switching is in the sidebar.
**Date:** 2026-06-08
**Author:** brainstormed with Claude

## Problem

Today a user belongs to exactly one tenant (`users.tenant_id` FK) with one global
role (`users.role`). We want a single user account to belong to **multiple**
tenants, with a **per-tenant role**, and a UI to **switch the active tenant**.

## Decisions (locked)

| Fork | Decision |
|---|---|
| Role scope | **Per tenant** — role lives on the membership, not the user |
| Active tenant transport | **In the JWT**; switching re-issues the token |
| Source of truth | **`tenant_memberships` is the only place tenant role lives**; drop `users.tenant_id` + `users.role`; add `users.is_super_admin` |
| Adding to tenants | **Both** — invite new users by token; add existing users directly by email |

## 1. Data model

```
users
  id, email, hashed_password, full_name, is_active
  is_super_admin  BOOLEAN NOT NULL DEFAULT false   -- NEW global flag
  -- REMOVED: tenant_id, role

tenant_memberships                                  -- NEW
  id            PK
  user_id       FK users.id      (indexed)
  tenant_id     FK tenants.id    (indexed)
  role          ENUM(admin, analyst, viewer)
  created_at    timestamptz default now()
  UNIQUE(user_id, tenant_id)
```

- New `TenantRole` enum = `admin | analyst | viewer`. `super_admin` is **not** a
  tenant role — it is the global `users.is_super_admin` flag.
- A super_admin needs no membership and can switch into any tenant.
- A regular user with zero memberships can log in but sees an empty state.
- Other tenant-scoped tables (cases, artifacts, iocs, audit_logs, etc.) are
  **unchanged** — they keep their own `tenant_id` columns.

## 2. Auth & JWT

- JWT payload: `{ sub, active_tenant_id }` (`active_tenant_id` may be null for a
  super_admin who hasn't selected a tenant).
- **Login** (`POST /auth/login/access-token`): default `active_tenant_id` =
  lowest-id membership for a regular user; lowest-id tenant in the system for a
  super_admin; null if none exist.
- **`POST /auth/switch-tenant { tenant_id }`** (NEW): validates the caller has a
  membership for `tenant_id` (or is super_admin), returns a **fresh token** with
  the new `active_tenant_id`. Returns 403 if not a member.
- **`GET /users/me`** extended to return:
  `is_super_admin`, `active_tenant_id`, `active_role`,
  `memberships: [{ tenant_id, tenant_name, tenant_slug, role }]`.

## 3. Dependency refactor (keeps endpoint churn small)

Preserve existing dependency **names and signatures** so the ~10 endpoint files
need no changes:

- `get_effective_tenant_id` — new internals: read `active_tenant_id` from the
  token, validate the membership exists (super_admin: validate the tenant exists,
  still honor `?tenant_id=` override), return the tenant id. 403 if the caller is
  not a member of the active tenant.
- `require_admin` / `require_analyst_or_above` — resolve the caller's role from
  the **active membership**; super_admin always passes.
- `require_super_admin` — checks `is_super_admin`.

New internal helper (pure, unit-testable):
`resolve_active_role(user, active_tenant_id, memberships) -> "super_admin" | TenantRole | None`.

Unchanged endpoint files: `cases.py`, `artifacts.py`, `iocs.py`, `stats.py`,
`alerts.py`, `audit_logs.py`, `integrations.py`, `copilot.py`.

## 4. Adding users to tenants

- `POST /invitations/` (admin):
  - **New email** → token invitation (unchanged). `accept_invitation` creates the
    account **and** a membership for `invitation.tenant_id` + `invitation.role`.
  - **Existing email** → create the membership immediately, return
    `{ added_directly: true }` (no token). The old 409 "user already exists" is
    removed for this path. If already a member → 409 "already a member".
- `PUT /users/{id}/role` → updates that user's membership role **in the active
  tenant** (admin only).
- `DELETE /users/{id}/membership` (NEW) → removes the user from the **active
  tenant** only (deletes the membership; account and other memberships untouched).
  Guards: cannot remove yourself; cannot remove the tenant's last admin.
- `GET /users/` (admin) → lists users in the active tenant via a join on
  `tenant_memberships`, returning each user's role in this tenant.

## 5. Frontend

- **`TenantSwitcher`** component in the sidebar (under the SOCHUB logo): shows the
  active tenant + role; lists the user's tenants (super_admin sees all tenants).
  Selecting one → `POST /auth/switch-tenant` → store the new token →
  `queryClient.invalidateQueries()` so every query refetches under the new tenant.
- `Layout` sidebar admin/super_admin items gate off `active_role` / `is_super_admin`
  from `/users/me`.
- `RequireRole` + `ProtectedRoute` read the active role from `/users/me`.
- `UserManagement` gains an "Add member" action (email + role) that calls the
  invitations endpoint and shows the `added_directly` vs token result.
- `types/index.ts`: extend `User` with `is_super_admin`, `active_tenant_id`,
  `active_role`, `memberships`. Add a `Membership` type.

## 6. Migration

New Alembic revision (down_revision = current head `a7b8c9d0e1f2`):

1. Add `users.is_super_admin` (default false); `UPDATE users SET is_super_admin = true WHERE role = 'super_admin'`.
2. Create `tenant_memberships` (+ unique index on `(user_id, tenant_id)`, indexes on `user_id`, `tenant_id`).
3. Backfill: `INSERT INTO tenant_memberships(user_id, tenant_id, role) SELECT id, tenant_id, role FROM users WHERE tenant_id IS NOT NULL AND role != 'super_admin'`.
4. Drop `users.tenant_id` and `users.role`.

Downgrade reverses: re-add columns, restore each user's primary (lowest-id)
membership into `users.tenant_id`/`role`, set `role='super_admin'` where
`is_super_admin`, drop the table and the flag.

## 7. Edge cases / error handling

- Active tenant revoked after token issued → tenant-scoped request returns 403;
  frontend catches it, clears the active tenant, forces re-pick (or logout).
- Switch to a non-member tenant → 403.
- Zero-membership non-super-admin → friendly empty state; no crashes.
- Tenant delete → memberships cascade (FK `ON DELETE CASCADE`).
- Removing the last admin of a tenant → blocked with 400.

## 8. Testing

`pytest` is a dependency but there is no test harness yet.

- Unit-test the pure `resolve_active_role` helper (super_admin, member, non-member,
  multi-tenant differing roles).
- Unit-test switch-tenant membership validation logic.
- Test that inviting an existing user creates a membership rather than erroring.
- Full DB integration tests (httpx + a test Postgres/SQLite) noted as a follow-up
  unless we stand up a test-DB fixture as part of this work.

## 9. Out of scope (explicit YAGNI)

- Org-level / cross-tenant roles beyond super_admin.
- Per-tenant SSO or invitation branding.
- Bulk membership import.
- Remembering a super_admin's "last active tenant" across logins (default to
  lowest-id tenant each login).
