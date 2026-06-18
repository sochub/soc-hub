# Alerts Triage UI + Multi-Webhook Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each tenant multiple named ingestion webhooks and a frontend triage queue to promote/dismiss the alerts those webhooks produce.

**Architecture:** Replace the single `Tenant.webhook_api_key` with a `webhooks` table (one row per named source). Alert ingestion resolves the `X-API-Key` to a webhook and stamps the alert's `source` from the webhook name. A new React "Alerts" page lists/triages alerts; the existing Integrations page manages webhooks.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (asyncpg/Postgres), React 19 + TanStack Query + React Router v7 + Tailwind.

## Global Constraints

- Backend lives in `backend/`, run inside Docker. After backend edits: `docker restart case_management_backend_1` (virtiofs breaks `--reload`).
- Frontend is an nginx image: `docker compose build frontend && docker compose up -d frontend` to see changes.
- All data tables carry `tenant_id`; cross-tenant access returns 404, not 403.
- No API integration-test harness exists (no conftest/test DB). Verify endpoints with the repo's `verify_*.py` script pattern (httpx against `http://backend:8000/api/v1`, run via `docker compose exec backend python verify_*.py`). Do NOT build a pytest DB harness.
- Webhook keys use prefix `whk_` and are stored/displayed in plaintext (matches existing implementation).
- Commit after every task.

---

### Task 1: `Webhook` model + migration (table, data-migrate, drop old column)

**Files:**
- Create: `backend/app/models/webhook.py`
- Modify: `backend/app/db/base.py` (register model)
- Modify: `backend/app/models/tenant.py` (remove `webhook_api_key` column)
- Create: `backend/alembic/versions/a1b2c3d4e5f6_webhooks_table.py`

**Interfaces:**
- Produces: `app.models.webhook.Webhook` with fields `id:int`, `tenant_id:int`, `name:str`, `api_key:str`, `created_at`.

- [ ] **Step 1: Create the model**

`backend/app/models/webhook.py`:
```python
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.db.base_class import Base


class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)  # source label, e.g. "Splunk"
    api_key = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: Register the model for Alembic autoload**

In `backend/app/db/base.py`, add after the `tenant` import:
```python
from app.models.webhook import Webhook  # noqa: F401
```

- [ ] **Step 3: Remove the old column from the Tenant model**

In `backend/app/models/tenant.py`, delete these lines:
```python
    # Per-tenant key for the alert ingestion webhook. Each tenant gets its own
    # secret so a single leaked key can only write to that one tenant.
    webhook_api_key = Column(String, unique=True, index=True, nullable=True)
```

- [ ] **Step 4: Write the migration**

`backend/alembic/versions/a1b2c3d4e5f6_webhooks_table.py` (current Alembic head is `b3c4d5e6f7a8` — confirmed via `docker compose exec backend alembic heads`):
```python
"""webhooks table (multi per-tenant) + migrate single key

Revision ID: a1b2c3d4e5f6
Revises: b3c4d5e6f7a8
Create Date: 2026-06-18 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f3g4h5i6j7k8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("api_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key"),
    )
    op.create_index("ix_webhooks_tenant_id", "webhooks", ["tenant_id"])
    op.create_index("ix_webhooks_api_key", "webhooks", ["api_key"])

    # Migrate each tenant's existing single key into a "Default" webhook.
    op.execute(
        "INSERT INTO webhooks (tenant_id, name, api_key) "
        "SELECT id, 'Default', webhook_api_key FROM tenants "
        "WHERE webhook_api_key IS NOT NULL"
    )

    op.drop_index("ix_tenants_webhook_api_key", table_name="tenants")
    op.drop_column("tenants", "webhook_api_key")


def downgrade() -> None:
    op.add_column("tenants", sa.Column("webhook_api_key", sa.String(), nullable=True))
    op.create_index("ix_tenants_webhook_api_key", "tenants", ["webhook_api_key"], unique=True)
    op.execute(
        "UPDATE tenants t SET webhook_api_key = w.api_key "
        "FROM webhooks w WHERE w.tenant_id = t.id"
    )
    op.drop_index("ix_webhooks_api_key", table_name="webhooks")
    op.drop_index("ix_webhooks_tenant_id", table_name="webhooks")
    op.drop_table("webhooks")
```

> Confirmed: the index is named `ix_tenants_webhook_api_key`.

- [ ] **Step 5: Run the migration**

Run: `docker compose exec backend alembic upgrade head`
Expected: completes without error.

- [ ] **Step 6: Verify table + data migration**

Run: `docker compose exec db psql -U user -d sicms -c "\d webhooks" -c "SELECT tenant_id, name FROM webhooks;"`
Expected: table shown; one `Default` row per tenant that previously had a key.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/webhook.py backend/app/db/base.py backend/app/models/tenant.py backend/alembic/versions/a1b2c3d4e5f6_webhooks_table.py
git commit -m "feat(webhooks): add webhooks table, migrate single per-tenant key"
```

---

### Task 2: Webhook-resolving auth dep + ingestion stamps source

**Files:**
- Modify: `backend/app/api/deps.py` (replace `get_tenant_from_webhook_key` with `get_webhook_from_key`)
- Modify: `backend/app/schemas/case.py` (add `AlertWebhookCreate`)
- Modify: `backend/app/api/v1/alerts.py` (ingestion uses webhook, stamps source)

**Interfaces:**
- Consumes: `app.models.webhook.Webhook` (Task 1).
- Produces: `deps.get_webhook_from_key() -> Webhook`; `schemas.case.AlertWebhookCreate(external_id:str, title:str, payload:Any)`.

- [ ] **Step 1: Replace the webhook auth dependency**

In `backend/app/api/deps.py`, add the Webhook import near the other model imports:
```python
from app.models.webhook import Webhook
```
Replace the whole `get_tenant_from_webhook_key` function with:
```python
async def get_webhook_from_key(
    db: AsyncSession = Depends(get_db),
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> Webhook:
    """Resolve the webhook that owns the supplied API key.

    Each webhook belongs to one tenant; the key alone determines both the
    destination tenant and the alert's source name. The owning tenant must be
    active.
    """
    result = await db.execute(
        select(Webhook)
        .join(Tenant, Tenant.id == Webhook.tenant_id)
        .where(
            Webhook.api_key == x_api_key,
            Tenant.is_active == True,  # noqa: E712
        )
    )
    webhook = result.scalars().first()
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return webhook
```

- [ ] **Step 2: Add the webhook ingestion schema**

In `backend/app/schemas/case.py`, after the `AlertCreate` class add:
```python
class AlertWebhookCreate(BaseModel):
    external_id: str
    title: str
    payload: Any = None
```

- [ ] **Step 3: Update the ingestion endpoint**

In `backend/app/api/v1/alerts.py`, replace the imports/`ingest_alert` so it uses the webhook dep and stamps `source`:
```python
from app.models.webhook import Webhook  # add to imports

@router.post("/webhook", response_model=case_schema.Alert)
async def ingest_alert(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_in: case_schema.AlertWebhookCreate,
    webhook: Webhook = Depends(deps.get_webhook_from_key),
) -> Any:
    """Ingest an alert via a tenant webhook.

    The webhook's API key determines both the destination tenant and the alert
    ``source`` (the webhook's name), so source attribution is authoritative and
    not caller-controlled.
    """
    alert = Alert(
        source=webhook.name,
        external_id=alert_in.external_id,
        title=alert_in.title,
        payload=alert_in.payload,
        status="pending",
        tenant_id=webhook.tenant_id,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert
```

- [ ] **Step 4: Restart and smoke-check it imports**

Run: `docker restart case_management_backend_1 && sleep 3 && docker logs --tail 20 case_management_backend_1`
Expected: no ImportError / startup traceback.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/deps.py backend/app/schemas/case.py backend/app/api/v1/alerts.py
git commit -m "feat(alerts): ingest via webhook key, stamp source from webhook name"
```

---

### Task 3: Dismiss endpoint

**Files:**
- Modify: `backend/app/api/v1/alerts.py`

**Interfaces:**
- Produces: `POST /alerts/{alert_id}/dismiss` → returns `Alert` with `status="dismissed"`.

- [ ] **Step 1: Add the dismiss endpoint**

Append to `backend/app/api/v1/alerts.py`:
```python
@router.post("/{alert_id}/dismiss", response_model=case_schema.Alert)
async def dismiss_alert(
    *,
    db: AsyncSession = Depends(deps.get_db),
    alert_id: int,
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Mark an alert as dismissed. Tenant-scoped."""
    result = await db.execute(
        select(Alert).where(Alert.id == alert_id, Alert.tenant_id == tenant_id)
    )
    alert = result.scalars().first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = "dismissed"
    await db.commit()
    await db.refresh(alert)
    return alert
```
Ensure `HTTPException` is imported (it is used by `promote_alert_to_case`; if not present add `from fastapi import ... HTTPException`).

- [ ] **Step 2: Restart**

Run: `docker restart case_management_backend_1 && sleep 3 && docker logs --tail 20 case_management_backend_1`
Expected: clean startup.

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/v1/alerts.py
git commit -m "feat(alerts): add dismiss endpoint"
```

---

### Task 4: Webhook management endpoints (list/create/revoke)

**Files:**
- Modify: `backend/app/api/v1/tenants.py` (un-privatize `generate_webhook_key`)
- Modify: `backend/app/api/v1/integrations.py` (add endpoints)

**Interfaces:**
- Consumes: `app.models.webhook.Webhook`, `deps.require_admin`, `deps.get_effective_tenant_id`.
- Produces: `GET /integrations/webhooks`, `POST /integrations/webhooks`, `DELETE /integrations/webhooks/{id}`.

- [ ] **Step 1: Make the key generator importable**

In `backend/app/api/v1/tenants.py`, rename `_generate_webhook_key` to `generate_webhook_key` (definition + the call inside `create_tenant`).

- [ ] **Step 2: Add management endpoints**

Append to `backend/app/api/v1/integrations.py`:
```python
from typing import List
from sqlalchemy.future import select
from app.models.webhook import Webhook
from app.api.v1.tenants import generate_webhook_key


class WebhookCreate(BaseModel):
    name: str


class WebhookOut(BaseModel):
    id: int
    name: str
    api_key: str
    created_at: Any
    class Config:
        from_attributes = True


@router.get("/webhooks", response_model=List[WebhookOut])
async def list_webhooks(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """List the active tenant's ingestion webhooks. Admin only."""
    result = await db.execute(
        select(Webhook).where(Webhook.tenant_id == tenant_id).order_by(Webhook.id)
    )
    return result.scalars().all()


@router.post("/webhooks", response_model=WebhookOut, status_code=201)
async def create_webhook(
    *,
    db: AsyncSession = Depends(deps.get_db),
    webhook_in: WebhookCreate,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Create a named webhook for the active tenant. Admin only."""
    webhook = Webhook(tenant_id=tenant_id, name=webhook_in.name, api_key=generate_webhook_key())
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.delete("/webhooks/{webhook_id}", status_code=204)
async def revoke_webhook(
    *,
    db: AsyncSession = Depends(deps.get_db),
    webhook_id: int,
    current_user: User = Depends(deps.require_admin),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> None:
    """Revoke (hard-delete) a webhook. Its key stops working immediately."""
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    webhook = result.scalars().first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(webhook)
    await db.commit()
```
The top of `integrations.py` already imports `BaseModel`, `Any`, `Depends`, `AsyncSession`, `HTTPException`, `deps`, `User` — reuse them (don't duplicate imports).

- [ ] **Step 3: Restart**

Run: `docker restart case_management_backend_1 && sleep 3 && docker logs --tail 20 case_management_backend_1`
Expected: clean startup.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/tenants.py backend/app/api/v1/integrations.py
git commit -m "feat(integrations): per-tenant webhook CRUD endpoints"
```

---

### Task 5: Remove the obsolete single-key rotate endpoint + tenant schema field

**Files:**
- Modify: `backend/app/api/v1/tenants.py` (remove `rotate_webhook_key`)
- Modify: `backend/app/schemas/tenant.py` (remove `webhook_api_key`)

- [ ] **Step 1: Remove the rotate endpoint**

In `backend/app/api/v1/tenants.py`, delete the entire `@router.post("/{tenant_id}/rotate-webhook-key", ...)` function.

- [ ] **Step 2: Remove the schema field**

In `backend/app/schemas/tenant.py`, delete the line:
```python
    webhook_api_key: Optional[str] = None
```

- [ ] **Step 3: Restart + check create-tenant still works**

Run: `docker restart case_management_backend_1 && sleep 3 && docker logs --tail 20 case_management_backend_1`
Expected: clean startup. (Tenant create no longer references the dropped column — `create_tenant` no longer passes `webhook_api_key`.)

> If `create_tenant` still passes `webhook_api_key=...`, remove that kwarg too.

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/v1/tenants.py backend/app/schemas/tenant.py
git commit -m "chore(tenants): drop obsolete single webhook key + rotate endpoint"
```

---

### Task 6: End-to-end backend verification script

**Files:**
- Create: `backend/verify_webhooks.py`

- [ ] **Step 1: Write the verification script**

`backend/verify_webhooks.py`:
```python
"""Runnable check: webhook CRUD + ingestion source-stamping + dismiss.

Run: docker compose exec backend python verify_webhooks.py
Requires an existing admin user; set creds below to match your dev data.
"""
import httpx

BASE = "http://backend:8000/api/v1"
ADMIN_EMAIL = "friquet@gmail.com"
ADMIN_PASSWORD = "changeme"  # set to your dev admin password


def run():
    # Login (OAuth2 password form on /auth/login/access-token; adjust if different)
    r = httpx.post(f"{BASE}/auth/login/access-token",
                   data={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # Create a webhook named after a source
    r = httpx.post(f"{BASE}/integrations/webhooks", json={"name": "Splunk"}, headers=headers)
    assert r.status_code == 201, r.text
    key = r.json()["api_key"]
    wid = r.json()["id"]
    assert key.startswith("whk_"), key
    print("created webhook", wid)

    # Ingest an alert using the key — no source in payload
    r = httpx.post(f"{BASE}/alerts/webhook",
                   json={"external_id": "ext-1", "title": "Brute force", "payload": {"ip": "1.2.3.4"}},
                   headers={"X-API-Key": key})
    assert r.status_code == 200, r.text
    alert = r.json()
    assert alert["source"] == "Splunk", f"source not stamped: {alert['source']}"
    print("ingested alert", alert["id"], "source=", alert["source"])

    # Bad key -> 401
    r = httpx.post(f"{BASE}/alerts/webhook",
                   json={"external_id": "x", "title": "y", "payload": {}},
                   headers={"X-API-Key": "whk_nope"})
    assert r.status_code == 401, r.text
    print("bad key correctly rejected")

    # Dismiss
    r = httpx.post(f"{BASE}/alerts/{alert['id']}/dismiss", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "dismissed", r.text
    print("dismissed ok")

    # Revoke webhook; key now rejected
    r = httpx.delete(f"{BASE}/integrations/webhooks/{wid}", headers=headers)
    assert r.status_code == 204, r.text
    r = httpx.post(f"{BASE}/alerts/webhook",
                   json={"external_id": "z", "title": "z", "payload": {}},
                   headers={"X-API-Key": key})
    assert r.status_code == 401, r.text
    print("revoke ok — ALL CHECKS PASSED")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run it**

Run: `docker compose exec backend python verify_webhooks.py`
Expected: ends with `ALL CHECKS PASSED`. (If login path/creds differ, fix the constants — confirm the auth route in `backend/app/api/v1/auth.py`.)

- [ ] **Step 3: Commit**

```bash
git add backend/verify_webhooks.py
git commit -m "test(webhooks): end-to-end verification script"
```

---

### Task 7: Alerts page — list, filters, dismiss, expand payload + nav/route

**Files:**
- Create: `frontend/src/features/alerts/AlertsList.tsx`
- Modify: `frontend/src/App.tsx` (route)
- Modify: `frontend/src/components/layout/Layout.tsx` (sidebar item)

**Interfaces:**
- Consumes backend: `GET /alerts/`, `POST /alerts/{id}/dismiss`.
- Produces: default-exported `AlertsList` component at route `/alerts`.

- [ ] **Step 1: Create the Alerts page**

`frontend/src/features/alerts/AlertsList.tsx`:
```tsx
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { cn } from '../../lib/utils';
import { ChevronDown, ChevronRight, Search, X, CheckCircle } from 'lucide-react';
import PromoteAlertModal from './PromoteAlertModal';

interface Alert {
    id: number;
    source: string;
    external_id: string;
    title: string;
    payload: any;
    status: string;
    case_id: number | null;
    created_at: string;
}

const statusColor: Record<string, string> = {
    pending: 'text-amber-700 bg-amber-50 border-amber-200',
    promoted: 'text-emerald-700 bg-emerald-50 border-emerald-200',
    dismissed: 'text-zinc-500 bg-zinc-100 border-zinc-200',
};

export default function AlertsList() {
    const qc = useQueryClient();
    const [statusFilter, setStatusFilter] = useState('all');
    const [sourceFilter, setSourceFilter] = useState('all');
    const [expanded, setExpanded] = useState<number | null>(null);
    const [promoting, setPromoting] = useState<Alert | null>(null);

    const { data: alerts, isLoading } = useQuery<Alert[]>({
        queryKey: ['alerts'],
        queryFn: async () => (await api.get('/alerts/')).data,
    });

    const dismiss = useMutation({
        mutationFn: async (id: number) => (await api.post(`/alerts/${id}/dismiss`)).data,
        onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
    });

    const sources = Array.from(new Set((alerts || []).map(a => a.source).filter(Boolean)));
    const filtered = (alerts || []).filter(a =>
        (statusFilter === 'all' || a.status === statusFilter) &&
        (sourceFilter === 'all' || a.source === sourceFilter)
    );

    if (isLoading) {
        return <div className="flex items-center justify-center h-[50vh]">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-zinc-300" /></div>;
    }

    return (
        <div className="space-y-4">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold text-zinc-900 tracking-tight">Alerts</h1>
                    <p className="text-zinc-500 text-sm mt-0.5">{filtered.length} alerts</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="relative">
                        <select value={sourceFilter} onChange={e => setSourceFilter(e.target.value)}
                            className="appearance-none bg-white border border-zinc-200 rounded-lg pl-3 pr-8 py-1.5 text-sm text-zinc-700 cursor-pointer focus:outline-none focus:ring-1 focus:ring-accent-500">
                            <option value="all">All Sources</option>
                            {sources.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none" />
                    </div>
                    <div className="relative">
                        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
                            className="appearance-none bg-white border border-zinc-200 rounded-lg pl-3 pr-8 py-1.5 text-sm text-zinc-700 cursor-pointer focus:outline-none focus:ring-1 focus:ring-accent-500">
                            <option value="all">All Statuses</option>
                            <option value="pending">Pending</option>
                            <option value="promoted">Promoted</option>
                            <option value="dismissed">Dismissed</option>
                        </select>
                        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none" />
                    </div>
                </div>
            </div>

            <div className="glass-panel rounded-lg border border-zinc-200 overflow-hidden">
                <table className="w-full text-sm text-left">
                    <thead className="bg-white border-b border-zinc-200 text-xs uppercase text-zinc-500 font-semibold tracking-wider">
                        <tr>
                            <th className="px-4 py-3 w-8"></th>
                            <th className="px-4 py-3 w-40">Source</th>
                            <th className="px-4 py-3">Title</th>
                            <th className="px-4 py-3 w-28">Status</th>
                            <th className="px-4 py-3 w-40">Received</th>
                            <th className="px-4 py-3 w-56">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-200">
                        {filtered.length === 0 ? (
                            <tr><td colSpan={6} className="px-6 py-12 text-center text-zinc-400">
                                <Search size={32} className="mb-2 opacity-50 mx-auto" />No alerts</td></tr>
                        ) : filtered.map(a => (
                            <>
                                <tr key={a.id} className="group hover:bg-zinc-100">
                                    <td className="px-4 py-2.5">
                                        <button onClick={() => setExpanded(expanded === a.id ? null : a.id)} className="text-zinc-400 hover:text-zinc-700">
                                            {expanded === a.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                        </button>
                                    </td>
                                    <td className="px-4 py-2.5"><span className="font-mono text-xs text-zinc-600">{a.source}</span></td>
                                    <td className="px-4 py-2.5 font-medium text-zinc-800">{a.title}</td>
                                    <td className="px-4 py-2.5">
                                        <span className={cn("inline-flex px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border", statusColor[a.status] || statusColor.dismissed)}>{a.status}</span>
                                    </td>
                                    <td className="px-4 py-2.5 text-zinc-500 text-xs whitespace-nowrap">{new Date(a.created_at).toLocaleString()}</td>
                                    <td className="px-4 py-2.5">
                                        {a.status === 'pending' ? (
                                            <div className="flex items-center gap-2">
                                                <button onClick={() => setPromoting(a)} className="px-2 py-1 text-xs font-semibold rounded bg-accent-600 text-white hover:bg-accent-700 flex items-center gap-1"><CheckCircle size={12} />Promote</button>
                                                <button onClick={() => dismiss.mutate(a.id)} className="px-2 py-1 text-xs font-semibold rounded bg-zinc-100 text-zinc-600 hover:bg-zinc-200 flex items-center gap-1"><X size={12} />Dismiss</button>
                                            </div>
                                        ) : a.case_id ? (
                                            <a href={`/cases/${a.case_id}`} className="text-xs text-accent-600 hover:underline">Case #{a.case_id}</a>
                                        ) : <span className="text-xs text-zinc-400">—</span>}
                                    </td>
                                </tr>
                                {expanded === a.id && (
                                    <tr key={`${a.id}-payload`}><td colSpan={6} className="px-6 py-3 bg-zinc-50">
                                        <pre className="text-xs text-zinc-600 overflow-x-auto">{JSON.stringify(a.payload, null, 2)}</pre>
                                    </td></tr>
                                )}
                            </>
                        ))}
                    </tbody>
                </table>
            </div>

            {promoting && <PromoteAlertModal alert={promoting} onClose={() => setPromoting(null)} />}
        </div>
    );
}
```

> `PromoteAlertModal` is built in Task 8. Until then, comment out the import + usage to compile, or implement Task 8 first.

- [ ] **Step 2: Add the route**

In `frontend/src/App.tsx`, add the import:
```tsx
import AlertsList from './features/alerts/AlertsList';
```
And the route after the `cases/:id` route:
```tsx
<Route path="alerts" element={<AlertsList />} />
```

- [ ] **Step 3: Add the sidebar item**

In `frontend/src/components/layout/Layout.tsx`, add `Bell` is already imported. Add to `baseSidebarItems` after the Cases entry:
```tsx
    { icon: Bell, label: 'Alerts', path: '/alerts' },
```

- [ ] **Step 4: Build + verify in browser**

Run: `docker compose build frontend && docker compose up -d frontend`
Expected: `/alerts` loads, shows ingested alerts (run Task 6's script first to have data), filters work, expand shows payload, Dismiss flips status.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/alerts/AlertsList.tsx frontend/src/App.tsx frontend/src/components/layout/Layout.tsx
git commit -m "feat(frontend): alerts triage page with filters, dismiss, payload view"
```

---

### Task 8: Promote modal — link to existing case or create new case from alert

**Files:**
- Create: `frontend/src/features/alerts/PromoteAlertModal.tsx`

**Interfaces:**
- Consumes backend: `GET /cases/`, `POST /cases/`, `POST /alerts/{id}/promote/{case_id}`.
- Props: `{ alert: { id, title, source, payload }, onClose: () => void }`.

- [ ] **Step 1: Create the modal**

`frontend/src/features/alerts/PromoteAlertModal.tsx`:
```tsx
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { X } from 'lucide-react';

interface Props {
    alert: { id: number; title: string; source: string; payload: any };
    onClose: () => void;
}
interface CaseLite { id: number; title: string; }

export default function PromoteAlertModal({ alert, onClose }: Props) {
    const qc = useQueryClient();
    const [mode, setMode] = useState<'existing' | 'new'>('existing');
    const [caseId, setCaseId] = useState<string>('');

    const { data: cases } = useQuery<CaseLite[]>({
        queryKey: ['cases'],
        queryFn: async () => (await api.get('/cases/')).data,
    });

    const promote = useMutation({
        mutationFn: async () => {
            let targetId = caseId;
            if (mode === 'new') {
                const res = await api.post('/cases/', {
                    title: alert.title,
                    description: `Created from ${alert.source} alert.\n\n${JSON.stringify(alert.payload, null, 2)}`,
                    severity: 'medium',
                    status: 'new',
                    tags: [],
                    source: alert.source,
                });
                targetId = String(res.data.id);
            }
            await api.post(`/alerts/${alert.id}/promote/${targetId}`);
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['alerts'] });
            qc.invalidateQueries({ queryKey: ['cases'] });
            onClose();
        },
    });

    const canSubmit = mode === 'new' || !!caseId;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
            <div className="glass-panel rounded-xl border border-zinc-200 w-full max-w-md p-6" onClick={e => e.stopPropagation()}>
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-lg font-bold text-zinc-900">Promote alert</h2>
                    <button onClick={onClose} className="text-zinc-400 hover:text-zinc-700"><X size={18} /></button>
                </div>
                <p className="text-sm text-zinc-500 mb-4 truncate">{alert.title}</p>

                <div className="flex gap-2 mb-4">
                    <button onClick={() => setMode('existing')} className={`flex-1 py-1.5 text-sm rounded-lg border ${mode === 'existing' ? 'bg-accent-600 text-white border-accent-600' : 'bg-white text-zinc-600 border-zinc-200'}`}>Existing case</button>
                    <button onClick={() => setMode('new')} className={`flex-1 py-1.5 text-sm rounded-lg border ${mode === 'new' ? 'bg-accent-600 text-white border-accent-600' : 'bg-white text-zinc-600 border-zinc-200'}`}>New case</button>
                </div>

                {mode === 'existing' && (
                    <select value={caseId} onChange={e => setCaseId(e.target.value)} className="w-full bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-1 focus:ring-accent-500">
                        <option value="">Select a case…</option>
                        {cases?.map(c => <option key={c.id} value={c.id}>#{c.id} — {c.title}</option>)}
                    </select>
                )}
                {mode === 'new' && (
                    <p className="text-xs text-zinc-500 mb-4">A new case titled “{alert.title}” will be created from this alert and linked.</p>
                )}

                <button disabled={!canSubmit || promote.isPending} onClick={() => promote.mutate()}
                    className="w-full py-2 rounded-lg bg-accent-600 text-white text-sm font-semibold hover:bg-accent-700 disabled:opacity-50">
                    {promote.isPending ? 'Working…' : 'Promote'}
                </button>
            </div>
        </div>
    );
}
```

- [ ] **Step 2: Build + verify**

Run: `docker compose build frontend && docker compose up -d frontend`
Expected: Promote opens the modal; "Existing case" links the alert (status → promoted, Case link appears); "New case" creates + links a case.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/alerts/PromoteAlertModal.tsx
git commit -m "feat(frontend): promote alert to existing or new case"
```

---

### Task 9: Integrations page — webhook manager

**Files:**
- Modify: `frontend/src/features/integrations/Integrations.tsx`

**Interfaces:**
- Consumes backend: `GET/POST/DELETE /integrations/webhooks`, `GET /users/me` (role gate).

- [ ] **Step 1: Replace the placeholder with the manager**

`frontend/src/features/integrations/Integrations.tsx`:
```tsx
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { User } from '../../types';
import { Plus, Trash2, Copy, Check } from 'lucide-react';

interface Webhook { id: number; name: string; api_key: string; created_at: string; }

export default function Integrations() {
    const qc = useQueryClient();
    const [name, setName] = useState('');
    const [copied, setCopied] = useState<number | null>(null);

    const { data: me } = useQuery({ queryKey: ['currentUser'], queryFn: async () => (await api.get('/users/me')).data as User });
    const isAdmin = me?.role === 'admin' || me?.is_super_admin;

    const { data: webhooks } = useQuery<Webhook[]>({
        queryKey: ['webhooks'],
        queryFn: async () => (await api.get('/integrations/webhooks')).data,
        enabled: !!isAdmin,
    });

    const create = useMutation({
        mutationFn: async (n: string) => (await api.post('/integrations/webhooks', { name: n })).data,
        onSuccess: () => { setName(''); qc.invalidateQueries({ queryKey: ['webhooks'] }); },
    });
    const revoke = useMutation({
        mutationFn: async (id: number) => api.delete(`/integrations/webhooks/${id}`),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['webhooks'] }),
    });

    const copy = (wh: Webhook) => { navigator.clipboard.writeText(wh.api_key); setCopied(wh.id); setTimeout(() => setCopied(null), 1500); };
    const ingestUrl = `${window.location.origin}/api/v1/alerts/webhook`;

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold text-zinc-900 tracking-tight">Integrations</h1>
                <p className="text-zinc-500 mt-1">Create webhooks so tools like a SIEM can push alerts into a tenant.</p>
            </div>

            {!isAdmin ? (
                <div className="glass-panel p-8 rounded-xl text-center text-zinc-500">Ask a tenant admin to manage webhooks.</div>
            ) : (
                <div className="space-y-6">
                    <div className="glass-panel p-5 rounded-xl border border-zinc-200">
                        <h2 className="font-semibold text-zinc-800 mb-3">New webhook</h2>
                        <div className="flex gap-2">
                            <input value={name} onChange={e => setName(e.target.value)} placeholder="Source name (e.g. Splunk)"
                                className="flex-1 bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-accent-500" />
                            <button disabled={!name.trim() || create.isPending} onClick={() => create.mutate(name.trim())}
                                className="px-3 py-2 rounded-lg bg-accent-600 text-white text-sm font-semibold hover:bg-accent-700 disabled:opacity-50 flex items-center gap-1.5"><Plus size={16} />Create</button>
                        </div>
                    </div>

                    <div className="glass-panel rounded-xl border border-zinc-200 overflow-hidden">
                        <table className="w-full text-sm text-left">
                            <thead className="bg-white border-b border-zinc-200 text-xs uppercase text-zinc-500 font-semibold tracking-wider">
                                <tr><th className="px-4 py-3">Name</th><th className="px-4 py-3">API Key</th><th className="px-4 py-3 w-32">Created</th><th className="px-4 py-3 w-20"></th></tr>
                            </thead>
                            <tbody className="divide-y divide-zinc-200">
                                {(webhooks || []).length === 0 ? (
                                    <tr><td colSpan={4} className="px-6 py-10 text-center text-zinc-400">No webhooks yet</td></tr>
                                ) : webhooks!.map(wh => (
                                    <tr key={wh.id} className="hover:bg-zinc-100">
                                        <td className="px-4 py-2.5 font-medium text-zinc-800">{wh.name}</td>
                                        <td className="px-4 py-2.5">
                                            <button onClick={() => copy(wh)} className="font-mono text-xs text-zinc-600 hover:text-accent-600 flex items-center gap-1.5">
                                                <span className="truncate max-w-[16rem]">{wh.api_key}</span>
                                                {copied === wh.id ? <Check size={12} className="text-emerald-600" /> : <Copy size={12} />}
                                            </button>
                                        </td>
                                        <td className="px-4 py-2.5 text-zinc-500 text-xs">{new Date(wh.created_at).toLocaleDateString()}</td>
                                        <td className="px-4 py-2.5">
                                            <button onClick={() => { if (confirm(`Revoke "${wh.name}"? Its key stops working.`)) revoke.mutate(wh.id); }}
                                                className="text-zinc-400 hover:text-red-600"><Trash2 size={15} /></button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <div className="glass-panel p-5 rounded-xl border border-zinc-200">
                        <h2 className="font-semibold text-zinc-800 mb-2">Pushing alerts</h2>
                        <p className="text-xs text-zinc-500 mb-2">POST to the endpoint below with your webhook's key:</p>
                        <pre className="text-xs text-zinc-600 bg-zinc-50 rounded-lg p-3 overflow-x-auto">{`curl -X POST ${ingestUrl} \\
  -H "X-API-Key: <your webhook key>" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"siem-123","title":"Suspicious login","payload":{"ip":"1.2.3.4"}}'`}</pre>
                    </div>
                </div>
            )}
        </div>
    );
}
```

- [ ] **Step 2: Build + verify**

Run: `docker compose build frontend && docker compose up -d frontend`
Expected: as admin, Integrations lists/creates/revokes webhooks, copy key works, curl sample shows the right URL. As non-admin, the "ask an admin" note shows.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/integrations/Integrations.tsx
git commit -m "feat(frontend): webhook manager on integrations page"
```

---

## Self-Review Notes

- **Spec coverage:** webhooks table + migration (T1); ingestion stamps source via webhook dep (T2); dismiss (T3); webhook CRUD (T4); rotate/field removal (T5); e2e verify (T6); Alerts page list/filter/dismiss/payload + nav/route (T7); promote existing/new (T8); integrations webhook manager (T9). All spec sections mapped.
- **Type consistency:** `Webhook` fields (`id/tenant_id/name/api_key/created_at`) consistent across T1/T2/T4; `AlertWebhookCreate(external_id,title,payload)` used in T2 and exercised in T6/T9 curl; frontend `Alert` shape matches `schemas.case.Alert`.
- **Known check to confirm during T1:** the dropped index name `ix_tenants_webhook_api_key` and current Alembic head; T6 login route/creds — both flagged inline.
