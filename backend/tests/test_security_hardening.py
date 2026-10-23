"""Hardening from the 2026-10 external assessment: API schema, invitation check, export quota, webhook keys."""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core import login_throttle, security
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.case import Alert
from app.models.invitation import Invitation, InvitationStatus
from app.models.membership import TenantMembership
from app.models.webhook import Webhook, verify_webhook_key
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run


async def _admin(ids):
    async with AsyncSessionLocal() as db:
        t = await make_tenant(db, ids)
        u = await make_user(db, ids)
        db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="admin"))
        await db.commit()
    return t, u, {"Authorization": f"Bearer {security.issue_access_token(u, t.id)}"}


async def _drop(t, u):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Alert).where(Alert.tenant_id == t.id))
        await db.execute(delete(Invitation).where(Invitation.tenant_id == t.id))
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id == u.id))
        await db.commit()


def test_api_docs_need_a_session():
    async def body(ids):
        t, u, h = await _admin(ids)
        try:
            async with client() as c:
                for path in ("/docs", "/redoc", "/api/v1/openapi.json"):
                    assert (await c.get(path)).status_code in (401, 404), path
                r = await c.get("/api/v1/openapi.json", headers=h)
                assert r.status_code == 200 and "/api/v1/cases/" in r.json()["paths"]
        finally:
            await _drop(t, u)
    run(body)


def test_invitation_check_reveals_nothing_unless_live():
    async def body(ids):
        t, u, _ = await _admin(ids)
        now = datetime.now(timezone.utc)
        toks = {k: secrets.token_urlsafe(32) for k in ("live", "used", "expired")}
        async with AsyncSessionLocal() as db:
            for k, status, exp in [("live", InvitationStatus.PENDING, now + timedelta(hours=1)),
                                   ("used", InvitationStatus.ACCEPTED, now + timedelta(hours=1)),
                                   ("expired", InvitationStatus.PENDING, now - timedelta(hours=1))]:
                db.add(Invitation(email=ids.email(k), tenant_id=t.id, role="analyst", token=toks[k],
                                  status=status, expires_at=exp))
            await db.commit()
        empty = {"email": "", "tenant_name": "", "role": "", "valid": False}
        try:
            async with client() as c:
                for k in ("used", "expired"):
                    assert (await c.get(f"/api/v1/invitations/validate/{toks[k]}")).json() == empty, k
                assert (await c.get("/api/v1/invitations/validate/nope")).json() == empty
                live = (await c.get(f"/api/v1/invitations/validate/{toks['live']}")).json()
                assert live["valid"] and live["email"].startswith(f"g-{ids.h}-live")
        finally:
            await _drop(t, u)
    run(body)


def test_audit_export_quota():
    class Blocked:
        async def reserve(self, key, ip):
            return 30

    async def body(ids):
        t, u, h = await _admin(ids)
        app.dependency_overrides[login_throttle.get_export_throttle] = lambda: Blocked()
        try:
            async with client() as c:
                r = await c.get("/api/v1/audit-logs/export", headers=h)
                assert r.status_code == 429 and r.headers["retry-after"] == "30"
        finally:
            await _drop(t, u)
    run(body)


def test_webhook_key_stored_hashed_and_shown_once():
    async def body(ids):
        t, u, h = await _admin(ids)
        try:
            async with client() as c:
                r = await c.post("/api/v1/integrations/webhooks", json={"name": "Splunk"}, headers=h)
                assert r.status_code == 201
                key = r.json()["api_key"]
                assert key.startswith("whk_") and r.json()["key_prefix"] == key[:12]
                listed = (await c.get("/api/v1/integrations/webhooks", headers=h)).json()
                assert listed[0]["api_key"] is None and listed[0]["key_prefix"] == key[:12]
                async with AsyncSessionLocal() as db:
                    row = (await db.execute(select(Webhook).where(Webhook.tenant_id == t.id))).scalar_one()
                    assert row.key_hash.startswith("$argon2id$") and verify_webhook_key(key, row.key_hash)
                alert = {"external_id": f"x-{ids.h}", "title": "t", "payload": {}}
                assert (await c.post("/api/v1/alerts/webhook", json=alert, headers={"X-API-Key": key})).status_code < 300
                assert (await c.post("/api/v1/alerts/webhook", json=alert,
                                     headers={"X-API-Key": key + "x"})).status_code == 401
        finally:
            await _drop(t, u)
    run(body)


def test_case_with_null_tags_does_not_break_the_list():
    async def body(ids):
        t, u, h = await _admin(ids)
        async with AsyncSessionLocal() as db:
            await make_case(db, ids, t, tags=None)
        try:
            async with client() as c:
                r = await c.get("/api/v1/cases/", headers=h)
                assert r.status_code == 200 and r.json()[0]["tags"] == []
        finally:
            await _drop(t, u)
    run(body)
