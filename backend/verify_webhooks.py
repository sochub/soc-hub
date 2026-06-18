"""End-to-end verification script: webhook CRUD + ingestion source-stamping + dismiss.

Run: docker compose exec backend python verify_webhooks.py

Self-bootstraps a throwaway admin user via direct DB access so no real user
accounts or passwords are needed.  All created rows are left in the DB but
they are idempotent — re-running this script is safe.
"""
import asyncio
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select

from app.core.security import get_password_hash
from app.models.tenant import Tenant
from app.models.user import User
from app.models.membership import TenantMembership

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_URL = "postgresql+asyncpg://user:password@db:5432/sicms"
BASE = "http://backend:8000/api/v1"

VERIFY_SLUG = "webhook-verify"
VERIFY_EMAIL = "webhook-verify@example.test"
VERIFY_PASSWORD = "verifypass123"
VERIFY_NAME = "Webhook Verify Admin"
TENANT_NAME = "Webhook Verify"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

async def bootstrap() -> None:
    """Idempotently create the throwaway tenant, user and membership."""
    engine = create_async_engine(DB_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # --- Tenant ---
        result = await session.execute(
            select(Tenant).where(Tenant.slug == VERIFY_SLUG)
        )
        tenant = result.scalars().first()
        if tenant is None:
            tenant = Tenant(name=TENANT_NAME, slug=VERIFY_SLUG, is_active=True)
            session.add(tenant)
            await session.flush()  # populate id before use
            print(f"[bootstrap] created tenant id={tenant.id}")
        else:
            print(f"[bootstrap] tenant already exists id={tenant.id}")

        # --- User ---
        result = await session.execute(
            select(User).where(User.email == VERIFY_EMAIL)
        )
        user = result.scalars().first()
        if user is None:
            user = User(
                email=VERIFY_EMAIL,
                hashed_password=get_password_hash(VERIFY_PASSWORD),
                full_name=VERIFY_NAME,
                is_active=True,
                is_super_admin=False,
            )
            session.add(user)
            await session.flush()
            print(f"[bootstrap] created user id={user.id}")
        else:
            print(f"[bootstrap] user already exists id={user.id}")

        # --- Membership ---
        result = await session.execute(
            select(TenantMembership).where(
                TenantMembership.user_id == user.id,
                TenantMembership.tenant_id == tenant.id,
            )
        )
        membership = result.scalars().first()
        if membership is None:
            membership = TenantMembership(
                user_id=user.id,
                tenant_id=tenant.id,
                role="admin",
            )
            session.add(membership)
            print(f"[bootstrap] created membership user={user.id} tenant={tenant.id} role=admin")
        else:
            print(f"[bootstrap] membership already exists id={membership.id}")

        await session.commit()

    await engine.dispose()


# ---------------------------------------------------------------------------
# HTTP flow
# ---------------------------------------------------------------------------

def run() -> None:
    # -- Login --
    r = httpx.post(
        f"{BASE}/auth/login/access-token",
        data={"username": VERIFY_EMAIL, "password": VERIFY_PASSWORD},
    )
    assert r.status_code == 200, (
        f"login failed: {r.status_code} {r.text}"
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[check] login OK")

    # -- Create webhook --
    r = httpx.post(f"{BASE}/integrations/webhooks", json={"name": "Splunk"}, headers=headers)
    assert r.status_code == 201, (
        f"create webhook failed: {r.status_code} {r.text}"
    )
    api_key = r.json()["api_key"]
    wid = r.json()["id"]
    assert api_key.startswith("whk_"), (
        f"api_key should start with 'whk_', got: {api_key!r}"
    )
    print(f"[check] created webhook id={wid} key_prefix={api_key[:8]}...")

    # -- Ingest alert (source stamped from webhook name, not payload) --
    r = httpx.post(
        f"{BASE}/alerts/webhook",
        json={"external_id": "ext-1", "title": "Brute force", "payload": {"ip": "1.2.3.4"}},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200, (
        f"ingest alert failed: {r.status_code} {r.text}"
    )
    alert = r.json()
    assert alert["source"] == "Splunk", (
        f"source not stamped from webhook name: got {alert['source']!r}, expected 'Splunk'"
    )
    alert_id = alert["id"]
    print(f"[check] ingested alert id={alert_id} source={alert['source']!r}  ✓ source-stamping correct")

    # -- Bad key → 401 --
    r = httpx.post(
        f"{BASE}/alerts/webhook",
        json={"external_id": "x", "title": "y", "payload": {}},
        headers={"X-API-Key": "whk_nope"},
    )
    assert r.status_code == 401, (
        f"bad key should return 401, got {r.status_code}: {r.text}"
    )
    print("[check] bad key correctly rejected with 401")

    # -- Dismiss --
    r = httpx.post(f"{BASE}/alerts/{alert_id}/dismiss", headers=headers)
    assert r.status_code == 200, (
        f"dismiss failed: {r.status_code} {r.text}"
    )
    assert r.json()["status"] == "dismissed", (
        f"dismiss: expected status='dismissed', got {r.json()['status']!r}"
    )
    print("[check] alert dismissed OK")

    # -- Revoke webhook --
    r = httpx.delete(f"{BASE}/integrations/webhooks/{wid}", headers=headers)
    assert r.status_code == 204, (
        f"revoke webhook failed: {r.status_code} {r.text}"
    )
    print(f"[check] webhook id={wid} revoked")

    # -- Revoked key → 401 --
    r = httpx.post(
        f"{BASE}/alerts/webhook",
        json={"external_id": "z", "title": "z", "payload": {}},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 401, (
        f"revoked key should return 401, got {r.status_code}: {r.text}"
    )
    print("[check] revoked key correctly rejected with 401")

    print()
    print("ALL CHECKS PASSED")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(bootstrap())
    run()
