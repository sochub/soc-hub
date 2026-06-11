# Multi-tenant Memberships + Tenant Switching — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status:** Backlog — not scheduled. See `design.md` in this folder for the approved design.

**Goal:** Let one user account belong to multiple tenants with a per-tenant role, and switch the active tenant from the UI.

**Architecture:** Tenant role moves from `users` onto a new `tenant_memberships` table (single source of truth). `super_admin` becomes the global `users.is_super_admin` flag. The active tenant rides in the JWT (`active_tenant_id`); switching re-issues the token. Existing auth **dependency names/signatures are preserved** (`get_effective_tenant_id`, `require_admin`, `require_analyst_or_above`, `require_super_admin`) so tenant-scoped endpoint files don't change.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, PostgreSQL (asyncpg), Pydantic v2, React 19 + TanStack Query + React Router v7, Tailwind.

---

## File structure

**Backend — create**
- `backend/app/models/membership.py` — `TenantMembership` model + `TenantRole` enum.
- `backend/app/schemas/membership.py` — membership Pydantic schemas.
- `backend/alembic/versions/b8c9d0e1f2a3_add_tenant_memberships.py` — migration.
- `backend/app/utils/roles.py` — pure `resolve_active_role()` helper.
- `backend/tests/__init__.py`, `backend/tests/test_roles.py` — unit tests.

**Backend — modify**
- `backend/app/models/user.py` — drop `tenant_id`/`role`, add `is_super_admin`, add `memberships` relationship.
- `backend/app/db/base.py` — register the new model.
- `backend/app/core/security.py` — token now carries `active_tenant_id`.
- `backend/app/api/deps.py` — rewrite tenant/role resolution against memberships.
- `backend/app/api/v1/auth.py` — login default active tenant + `switch-tenant`.
- `backend/app/api/v1/users.py` — `/me` payload, role/membership management.
- `backend/app/api/v1/invitations.py` — existing-user → membership; accept creates membership.
- `backend/app/schemas/user.py` — `/me` response shape.
- `backend/app/scripts/create_super_admin.py` — set `is_super_admin`, no tenant.

**Frontend — create**
- `frontend/src/features/tenants/TenantSwitcher.tsx`
- `frontend/src/api/auth.ts` — `switchTenant()` helper.

**Frontend — modify**
- `frontend/src/types/index.ts`, `frontend/src/components/layout/Layout.tsx`,
  `frontend/src/components/auth/RequireRole.tsx`,
  `frontend/src/features/admin/UserManagement.tsx`,
  `frontend/src/features/admin/InviteUserModal.tsx`.

---

## Task 1: TenantRole enum + TenantMembership model

**Files:**
- Create: `backend/app/models/membership.py`
- Modify: `backend/app/models/user.py`
- Modify: `backend/app/db/base.py`

- [ ] **Step 1: Create the model and enum**

```python
# backend/app/models/membership.py
import enum
from sqlalchemy import Column, Integer, Enum, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base


class TenantRole(str, enum.Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(
        Enum(TenantRole, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=TenantRole.VIEWER,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="memberships")
    tenant = relationship("Tenant")
```

- [ ] **Step 2: Update the User model**

In `backend/app/models/user.py`, remove the `tenant_id` column, the `role` column, the `tenant` relationship, and the `UserRole` enum's `SUPER_ADMIN`/tenant-role usage for the user row. Replace with:

```python
from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.orm import relationship
from app.db.base_class import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String)
    is_active = Column(Boolean, default=True)
    is_super_admin = Column(Boolean, nullable=False, default=False)

    memberships = relationship(
        "TenantMembership", back_populates="user", cascade="all, delete-orphan"
    )
```

Keep the `TenantRole` enum in `membership.py` as the canonical tenant-role enum. Delete the old `UserRole` enum **only after** updating all importers (Tasks 4–8); until then leave it in place to avoid breaking imports mid-refactor.

- [ ] **Step 3: Register the model**

In `backend/app/db/base.py`, add `from app.models.membership import TenantMembership  # noqa: F401` next to the other model imports.

- [ ] **Step 4: Verify compile**

Run: `python -m py_compile backend/app/models/membership.py backend/app/models/user.py backend/app/db/base.py`
Expected: no output (success).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/membership.py backend/app/models/user.py backend/app/db/base.py
git commit -m "feat(model): add TenantMembership, is_super_admin flag"
```

---

## Task 2: Pure role-resolution helper (TDD)

**Files:**
- Create: `backend/app/utils/roles.py`
- Test: `backend/tests/test_roles.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_roles.py
from app.utils.roles import resolve_active_role
from app.models.membership import TenantRole


class FakeMembership:
    def __init__(self, tenant_id, role):
        self.tenant_id = tenant_id
        self.role = role


def test_super_admin_always_super_admin():
    assert resolve_active_role(True, 5, []) == "super_admin"


def test_member_role_for_active_tenant():
    ms = [FakeMembership(1, TenantRole.ADMIN), FakeMembership(2, TenantRole.VIEWER)]
    assert resolve_active_role(False, 2, ms) == TenantRole.VIEWER


def test_non_member_returns_none():
    ms = [FakeMembership(1, TenantRole.ADMIN)]
    assert resolve_active_role(False, 99, ms) is None


def test_no_active_tenant_returns_none():
    ms = [FakeMembership(1, TenantRole.ADMIN)]
    assert resolve_active_role(False, None, ms) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_roles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.roles'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/utils/roles.py
from typing import Iterable, Optional, Union
from app.models.membership import TenantRole


def resolve_active_role(
    is_super_admin: bool,
    active_tenant_id: Optional[int],
    memberships: Iterable,
) -> Optional[Union[str, TenantRole]]:
    """Return 'super_admin', the TenantRole for the active tenant, or None."""
    if is_super_admin:
        return "super_admin"
    if active_tenant_id is None:
        return None
    for m in memberships:
        if m.tenant_id == active_tenant_id:
            return m.role
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_roles.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/utils/roles.py backend/tests/test_roles.py backend/tests/__init__.py
git commit -m "feat(auth): add resolve_active_role helper with tests"
```

---

## Task 3: Migration — memberships table + backfill

**Files:**
- Create: `backend/alembic/versions/b8c9d0e1f2a3_add_tenant_memberships.py`

- [ ] **Step 1: Write the migration**

```python
"""add tenant memberships, is_super_admin; drop users.tenant_id/role

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-08 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. global super-admin flag
    op.add_column("users", sa.Column("is_super_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE users SET is_super_admin = true WHERE role = 'super_admin'")

    # 2. memberships table
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
    )
    op.create_index(op.f("ix_tenant_memberships_user_id"), "tenant_memberships", ["user_id"])
    op.create_index(op.f("ix_tenant_memberships_tenant_id"), "tenant_memberships", ["tenant_id"])

    # 3. backfill from existing single-tenant users
    op.execute(
        "INSERT INTO tenant_memberships (user_id, tenant_id, role) "
        "SELECT id, tenant_id, role FROM users "
        "WHERE tenant_id IS NOT NULL AND role != 'super_admin'"
    )

    # 4. drop old columns
    op.drop_column("users", "tenant_id")
    op.drop_column("users", "role")


def downgrade() -> None:
    op.add_column("users", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("role", sa.String(), nullable=True, server_default="analyst"))
    op.create_foreign_key("users_tenant_id_fkey", "users", "tenants", ["tenant_id"], ["id"])
    # restore each user's lowest-id membership as their primary
    op.execute(
        "UPDATE users u SET tenant_id = m.tenant_id, role = m.role "
        "FROM (SELECT DISTINCT ON (user_id) user_id, tenant_id, role "
        "FROM tenant_memberships ORDER BY user_id, tenant_id) m "
        "WHERE u.id = m.user_id"
    )
    op.execute("UPDATE users SET role = 'super_admin' WHERE is_super_admin = true")
    op.drop_index(op.f("ix_tenant_memberships_tenant_id"), table_name="tenant_memberships")
    op.drop_index(op.f("ix_tenant_memberships_user_id"), table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_column("users", "is_super_admin")
```

- [ ] **Step 2: Verify compile**

Run: `python -m py_compile backend/alembic/versions/b8c9d0e1f2a3_add_tenant_memberships.py`
Expected: no output.

- [ ] **Step 3: Apply against a scratch DB and verify**

Run: `cd backend && alembic upgrade head`
Then: `alembic current` → expect `b8c9d0e1f2a3 (head)`.
Verify with psql: `\d tenant_memberships` shows the unique constraint; `\d users` no longer has `tenant_id`/`role` but has `is_super_admin`.

- [ ] **Step 4: Verify downgrade reverses cleanly**

Run: `cd backend && alembic downgrade -1 && alembic upgrade head`
Expected: both succeed with no error.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/b8c9d0e1f2a3_add_tenant_memberships.py
git commit -m "feat(db): migration for tenant_memberships + is_super_admin"
```

---

## Task 4: Token carries active_tenant_id

**Files:**
- Modify: `backend/app/core/security.py`
- Modify: `backend/app/schemas/user.py` (TokenData)

- [ ] **Step 1: Extend token creation**

`create_access_token` already encodes an arbitrary `data` dict, so no signature change is needed — callers pass `active_tenant_id`. Add a convenience near the bottom of `security.py`:

```python
def build_token_payload(email: str, active_tenant_id: int | None) -> dict:
    return {"sub": email, "active_tenant_id": active_tenant_id}
```

- [ ] **Step 2: Update TokenData schema**

In `backend/app/schemas/user.py`:

```python
class TokenData(BaseModel):
    email: Optional[str] = None
    active_tenant_id: Optional[int] = None
```

- [ ] **Step 3: Verify compile**

Run: `python -m py_compile backend/app/core/security.py backend/app/schemas/user.py`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/security.py backend/app/schemas/user.py
git commit -m "feat(auth): token carries active_tenant_id"
```

---

## Task 5: Rewrite deps — tenant + role resolution against memberships

**Files:**
- Modify: `backend/app/api/deps.py`

- [ ] **Step 1: Load memberships on the current user**

In `get_current_user`, eager-load memberships and stash the token's `active_tenant_id` on the user object for downstream deps:

```python
from sqlalchemy.orm import selectinload
from app.models.membership import TenantMembership, TenantRole

# inside get_current_user, after decoding:
active_tenant_id = payload.get("active_tenant_id")
result = await db.execute(
    select(User).options(selectinload(User.memberships)).where(User.email == token_data.email)
)
user = result.scalars().first()
if user is None:
    raise credentials_exception
user._active_tenant_id = active_tenant_id  # transient, request-scoped
return user
```

- [ ] **Step 2: Rewrite `get_effective_tenant_id`**

```python
def get_effective_tenant_id(
    current_user: User = Depends(get_current_active_user),
    tenant_id: Optional[int] = Query(None),
) -> int:
    active = getattr(current_user, "_active_tenant_id", None)
    if current_user.is_super_admin:
        chosen = tenant_id if tenant_id is not None else active
        if chosen is None:
            raise HTTPException(status_code=400, detail="Super admin must select a tenant.")
        return chosen
    if active is None:
        raise HTTPException(status_code=400, detail="No active tenant selected.")
    if not any(m.tenant_id == active for m in current_user.memberships):
        raise HTTPException(status_code=403, detail="Not a member of the active tenant.")
    return active
```

- [ ] **Step 3: Rewrite role checks using `resolve_active_role`**

```python
from app.utils.roles import resolve_active_role

def _active_role(current_user: User):
    active = getattr(current_user, "_active_tenant_id", None)
    return resolve_active_role(current_user.is_super_admin, active, current_user.memberships)

def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    role = _active_role(current_user)
    if role == "super_admin" or role == TenantRole.ADMIN:
        return current_user
    raise HTTPException(status_code=403, detail="Insufficient permissions")

def require_analyst_or_above(current_user: User = Depends(get_current_active_user)) -> User:
    role = _active_role(current_user)
    if role in ("super_admin", TenantRole.ADMIN, TenantRole.ANALYST):
        return current_user
    raise HTTPException(status_code=403, detail="Insufficient permissions")

def require_super_admin(current_user: User = Depends(get_current_active_user)) -> User:
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_user
```

Delete the old `require_role` factory and module-level `require_admin = require_role(...)` assignments; the functions above replace them. Keep `get_tenant_from_webhook_key` as-is.

- [ ] **Step 4: Verify compile**

Run: `python -m py_compile backend/app/api/deps.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/deps.py
git commit -m "feat(auth): resolve tenant+role from active membership"
```

---

## Task 6: Login default tenant + switch-tenant endpoint

**Files:**
- Modify: `backend/app/api/v1/auth.py`

- [ ] **Step 1: Default active tenant at login**

Replace the token construction in `login_access_token`:

```python
from app.models.tenant import Tenant

# after verifying the user:
if user.is_super_admin:
    res = await db.execute(select(Tenant.id).order_by(Tenant.id).limit(1))
    active_tenant_id = res.scalars().first()
else:
    await db.refresh(user, ["memberships"])
    ms = sorted(user.memberships, key=lambda m: m.tenant_id)
    active_tenant_id = ms[0].tenant_id if ms else None

access_token = security.create_access_token(
    {"sub": user.email, "active_tenant_id": active_tenant_id},
    expires_delta=access_token_expires,
)
```

- [ ] **Step 2: Add the switch-tenant endpoint**

```python
from pydantic import BaseModel

class SwitchTenantRequest(BaseModel):
    tenant_id: int

@router.post("/switch-tenant", response_model=Token)
async def switch_tenant(
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: SwitchTenantRequest,
    current_user: User = Depends(deps.get_current_active_user),
) -> Any:
    if not current_user.is_super_admin:
        await db.refresh(current_user, ["memberships"])
        if not any(m.tenant_id == body.tenant_id for m in current_user.memberships):
            raise HTTPException(status_code=403, detail="You are not a member of that tenant.")
    else:
        res = await db.execute(select(Tenant).where(Tenant.id == body.tenant_id))
        if not res.scalars().first():
            raise HTTPException(status_code=404, detail="Tenant not found")

    token = security.create_access_token(
        {"sub": current_user.email, "active_tenant_id": body.tenant_id},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": token, "token_type": "bearer"}
```

- [ ] **Step 3: Verify compile**

Run: `python -m py_compile backend/app/api/v1/auth.py`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/auth.py
git commit -m "feat(auth): default active tenant + switch-tenant endpoint"
```

---

## Task 7: `/users/me` payload + membership management

**Files:**
- Modify: `backend/app/schemas/user.py`
- Modify: `backend/app/schemas/membership.py` (create)
- Modify: `backend/app/api/v1/users.py`

- [ ] **Step 1: Membership + me schemas**

```python
# backend/app/schemas/membership.py
from pydantic import BaseModel

class MembershipOut(BaseModel):
    tenant_id: int
    tenant_name: str
    tenant_slug: str
    role: str
```

```python
# add to backend/app/schemas/user.py
from typing import List
from app.schemas.membership import MembershipOut

class UserMe(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool
    is_super_admin: bool
    active_tenant_id: Optional[int] = None
    active_role: Optional[str] = None
    memberships: List[MembershipOut] = []
```

- [ ] **Step 2: Rewrite `read_user_me`**

```python
@router.get("/me", response_model=UserMe)
async def read_user_me(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    active = getattr(current_user, "_active_tenant_id", None)
    rows = await db.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.user_id == current_user.id)
    )
    memberships = [
        MembershipOut(tenant_id=t.id, tenant_name=t.name, tenant_slug=t.slug, role=m.role.value)
        for m, t in rows.all()
    ]
    from app.utils.roles import resolve_active_role
    role = resolve_active_role(current_user.is_super_admin, active, current_user.memberships)
    active_role = role if isinstance(role, str) else (role.value if role else None)
    return UserMe(
        id=current_user.id, email=current_user.email, full_name=current_user.full_name,
        is_active=current_user.is_active, is_super_admin=current_user.is_super_admin,
        active_tenant_id=active, active_role=active_role, memberships=memberships,
    )
```

- [ ] **Step 3: Rewrite list/role/remove to use memberships**

`read_users` (admin): join memberships for the active tenant.
`update_user_role`: update the membership row for `(user_id, active tenant)`.
Replace `deactivate_user`/`activate_user` with `remove_member`:

```python
@router.delete("/{user_id}/membership", status_code=204)
async def remove_member(
    *, db: AsyncSession = Depends(deps.get_db), user_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot remove yourself.")
    res = await db.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == user_id, TenantMembership.tenant_id == tenant_id
        )
    )
    m = res.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Membership not found")
    if m.role == TenantRole.ADMIN:
        cnt = await db.execute(
            select(sa.func.count()).select_from(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id, TenantMembership.role == TenantRole.ADMIN
            )
        )
        if cnt.scalar() <= 1:
            raise HTTPException(status_code=400, detail="Cannot remove the last admin.")
    await db.delete(m)
    await db.commit()
```

(Imports: `import sqlalchemy as sa`, `from app.models.membership import TenantMembership, TenantRole`, `from app.models.tenant import Tenant`.)

- [ ] **Step 4: Verify compile**

Run: `python -m py_compile backend/app/schemas/membership.py backend/app/schemas/user.py backend/app/api/v1/users.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/membership.py backend/app/schemas/user.py backend/app/api/v1/users.py
git commit -m "feat(users): /me memberships + per-tenant role/membership management"
```

---

## Task 8: Invitations — existing user → membership; create super admin

**Files:**
- Modify: `backend/app/api/v1/invitations.py`
- Modify: `backend/app/scripts/create_super_admin.py`

- [ ] **Step 1: create_invitation handles existing users**

Replace the "user already exists → 409" block:

```python
existing_user = (await db.execute(select(User).where(User.email == invitation_in.email))).scalars().first()
if existing_user:
    dup = await db.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == existing_user.id,
            TenantMembership.tenant_id == tenant_id,
        )
    )
    if dup.scalars().first():
        raise HTTPException(status_code=409, detail="User is already a member of this tenant.")
    db.add(TenantMembership(user_id=existing_user.id, tenant_id=tenant_id, role=TenantRole(invitation_in.role.value)))
    await db.commit()
    return invitation_schema.InvitationResponse(  # added_directly marker
        id=0, email=invitation_in.email, tenant_id=tenant_id, role=invitation_in.role.value,
        token="", status="added_directly", created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc), invite_link=None,
    )
```

(Add an `added_directly: bool = False` field to `InvitationResponse`, or reuse `status`.)

- [ ] **Step 2: accept_invitation creates user + membership**

```python
user = User(
    email=invitation.email,
    hashed_password=security.get_password_hash(accept_in.password),
    full_name=accept_in.full_name,
    is_active=True,
    is_super_admin=False,
)
db.add(user)
await db.flush()
db.add(TenantMembership(user_id=user.id, tenant_id=invitation.tenant_id, role=TenantRole(invitation.role)))
invitation.status = InvitationStatus.ACCEPTED
await db.commit()
```

- [ ] **Step 3: Update create_super_admin script**

The user row no longer has `tenant_id`/`role`. Replace the `User(...)` construction and the upgrade path:

```python
user = User(email=email, hashed_password=get_password_hash(password),
            full_name=full_name, is_active=True, is_super_admin=True)
# upgrade path: existing.is_super_admin = True
```

- [ ] **Step 4: Verify compile**

Run: `python -m py_compile backend/app/api/v1/invitations.py backend/app/scripts/create_super_admin.py`
Expected: no output.

- [ ] **Step 5: Full migrate + smoke test**

Run: `cd backend && alembic upgrade head`
Run: `python -m app.scripts.create_super_admin --email a@b.io --name Admin` (enter a strong password).
Expected: "Super admin created".

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/invitations.py backend/app/scripts/create_super_admin.py
git commit -m "feat(invitations): existing-user membership + super-admin flag"
```

---

## Task 9: Frontend types + auth helper

**Files:**
- Modify: `frontend/src/types/index.ts`
- Create: `frontend/src/api/auth.ts`

- [ ] **Step 1: Types**

```typescript
// frontend/src/types/index.ts — add
export interface Membership {
  tenant_id: number;
  tenant_name: string;
  tenant_slug: string;
  role: string;
}
export interface User {
  id: number;
  email: string;
  full_name?: string;
  is_active: boolean;
  is_super_admin: boolean;
  active_tenant_id?: number | null;
  active_role?: string | null;
  memberships: Membership[];
}
```

- [ ] **Step 2: switchTenant helper**

```typescript
// frontend/src/api/auth.ts
import { api } from './client';

export async function switchTenant(tenantId: number): Promise<void> {
  const res = await api.post('/auth/switch-tenant', { tenant_id: tenantId });
  localStorage.setItem('token', res.data.access_token);
}
```

- [ ] **Step 3: Build check**

Run: `cd frontend && npx tsc -b`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/api/auth.ts
git commit -m "feat(fe): user memberships type + switchTenant helper"
```

---

## Task 10: TenantSwitcher component + sidebar gating

**Files:**
- Create: `frontend/src/features/tenants/TenantSwitcher.tsx`
- Modify: `frontend/src/components/layout/Layout.tsx`
- Modify: `frontend/src/components/auth/RequireRole.tsx`

- [ ] **Step 1: TenantSwitcher**

```tsx
// frontend/src/features/tenants/TenantSwitcher.tsx
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronsUpDown } from 'lucide-react';
import { api } from '../../api/client';
import { switchTenant } from '../../api/auth';
import type { User } from '../../types';
import { cn } from '../../lib/utils';

export default function TenantSwitcher() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data: me } = useQuery({
    queryKey: ['currentUser'],
    queryFn: async () => (await api.get('/users/me')).data as User,
    staleTime: 5 * 60 * 1000,
  });
  if (!me || me.memberships.length <= 1) return null;
  const active = me.memberships.find((m) => m.tenant_id === me.active_tenant_id);

  const choose = async (tenantId: number) => {
    await switchTenant(tenantId);
    setOpen(false);
    await qc.invalidateQueries();
  };

  return (
    <div className="relative px-4 pb-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg border border-slate-800 bg-slate-900/60 text-sm text-slate-200 hover:bg-slate-800/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
        aria-haspopup="listbox" aria-expanded={open}
      >
        <span className="truncate">{active?.tenant_name ?? 'Select tenant'}</span>
        <ChevronsUpDown size={16} className="text-slate-500 shrink-0" />
      </button>
      {open && (
        <ul role="listbox" className="absolute z-50 left-4 right-4 mt-1 max-h-64 overflow-auto rounded-lg border border-slate-800 bg-slate-900 shadow-xl py-1">
          {me.memberships.map((m) => (
            <li key={m.tenant_id} role="option" aria-selected={m.tenant_id === me.active_tenant_id}>
              <button
                onClick={() => choose(m.tenant_id)}
                className={cn('w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-slate-800/60', m.tenant_id === me.active_tenant_id ? 'text-brand-300' : 'text-slate-300')}
              >
                <span className="truncate">{m.tenant_name}</span>
                <span className="flex items-center gap-2">
                  <span className="text-xs text-slate-500 capitalize">{m.role}</span>
                  {m.tenant_id === me.active_tenant_id && <Check size={14} />}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

> Super-admin note: super admins may have zero memberships but can access all
> tenants. A follow-up can fetch `/tenants` for the super-admin switcher list;
> for v1 the switcher only renders for users with 2+ memberships.

- [ ] **Step 2: Mount in Layout + gate sidebar on active_role**

In `Layout.tsx`, render `<TenantSwitcher />` right under the logo block. Change role gating from `currentUser.role === 'admin'/'super_admin'` to:

```tsx
if (currentUser?.active_role === 'admin' || currentUser?.is_super_admin) sidebarItems.push(...adminItems);
if (currentUser?.is_super_admin) sidebarItems.push(...superAdminItems);
```

Update `displayRole` to use `currentUser?.active_role` (or "Super Admin" when `is_super_admin`).

- [ ] **Step 3: RequireRole reads active role**

```tsx
const allowed = user && (
  user.is_super_admin && roles.includes('super_admin') ||
  (user.active_role && roles.includes(user.active_role))
);
if (!allowed) return <Navigate to="/" replace />;
```

- [ ] **Step 4: Build check**

Run: `cd frontend && npx tsc -b && npx vite build`
Expected: exit 0 both.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/tenants/TenantSwitcher.tsx frontend/src/components/layout/Layout.tsx frontend/src/components/auth/RequireRole.tsx
git commit -m "feat(fe): tenant switcher + active-role sidebar gating"
```

---

## Task 11: UserManagement "Add member" + role display

**Files:**
- Modify: `frontend/src/features/admin/UserManagement.tsx`
- Modify: `frontend/src/features/admin/InviteUserModal.tsx`

- [ ] **Step 1:** Add an "Add member" action that posts to `/invitations/` with `{ email, role }` and surfaces both outcomes: `status === 'added_directly'` → toast "Added to tenant"; otherwise show the invite link as today.
- [ ] **Step 2:** Show each user's role from the membership-scoped list response; wire the remove action to `DELETE /users/{id}/membership`.
- [ ] **Step 3: Build check** — `cd frontend && npx tsc -b && npx vite build` → exit 0.
- [ ] **Step 4: Commit** — `git commit -m "feat(fe): add member + per-tenant role management UI"`.

---

## Task 12: Docs + manual verification

**Files:**
- Modify: `README.md`
- Modify: `MEMORY.md` conventions note

- [ ] **Step 1:** Update README "Roles" section: roles are per-tenant memberships; super_admin is a global flag; document `POST /auth/switch-tenant` and the "Add member" flow.
- [ ] **Step 2: Manual E2E** (docker compose up): create super admin → create two tenants → invite a new user to tenant A → from tenant A admin, "Add member" the same email to tenant B → log in as that user → confirm the TenantSwitcher shows both, switching changes the visible cases, and the sidebar admin items follow the per-tenant role.
- [ ] **Step 3: Commit** — `git commit -m "docs: multi-tenant membership + switching"`.

---

## Self-review notes

- **Spec coverage:** data model (T1,T3), JWT/switch (T4,T6), deps refactor (T5), add-to-tenant both-paths (T8,T11), frontend switcher (T10), `/me` payload (T7), migration+backfill (T3), edge cases — last-admin guard (T7), non-member 403 (T5), zero-membership (T10 switcher hidden + empty state to add in T11). Testing (T2 pure helper; DB integration noted as follow-up).
- **Naming consistency:** `resolve_active_role`, `get_effective_tenant_id`, `TenantRole`, `TenantMembership`, `is_super_admin`, `active_tenant_id`, `switchTenant`, `MembershipOut` used consistently across tasks.
- **Known follow-ups (not blocking):** super-admin switcher listing all tenants; DB-backed integration test fixture; zero-membership empty-state screen.
