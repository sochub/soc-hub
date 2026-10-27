"""Suppression rules: matching, ingest auto-dismiss (no alert.ingested), expiry, preview, used-rule protection,
tenant isolation and roles."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.api import deps
from app.api.v1 import alerts as alerts_api
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Alert
from app.models.suppression_rule import SuppressionRule
from app.models.tenant import Tenant
from app.models.user import User
from app.models.webhook import Webhook, hash_webhook_key, webhook_key_prefix
from app.schemas.suppression import Condition
from app.services.suppression_service import matches

ALERT = {"source": "Okta", "title": "Impossible travel", "severity": "medium",
         "payload": {"user": {"email": "Svc-Backup@corp.example"}, "ip": "10.1.2.3", "tags": ["a", "b"]}}


def c(field, op, value):
    return {"field": field, "op": op, "value": value}


def test_matches_all_ops():
    assert matches([c("source", "equals", "okta"), c("title", "contains", "travel")], ALERT)
    assert matches([c("payload.user.email", "starts_with", "svc-")], ALERT)
    assert matches([c("payload.user.email", "ends_with", "@corp.example")], ALERT)
    assert matches([c("payload.ip", "in_cidr", "10.0.0.0/8")], ALERT)
    assert not matches([c("payload.ip", "in_cidr", "192.168.0.0/16")], ALERT)
    assert matches([c("payload.tags.1", "equals", "b")], ALERT)
    assert matches([c("severity", "not_equals", "critical")], ALERT)
    # every condition must hold; missing fields match nothing but not_equals
    assert not matches([c("source", "equals", "okta"), c("payload.nope", "equals", "x")], ALERT)
    assert matches([c("payload.nope", "not_equals", "x")], ALERT)
    assert not matches([c("payload.user", "contains", "svc")], ALERT)  # objects never match
    assert not matches([], ALERT)


def test_condition_validation():
    for bad in ({"field": "owner_id", "op": "equals", "value": "1"},
                {"field": "payload", "op": "equals", "value": "x"},
                {"field": "title", "op": "regex", "value": ".*"},
                {"field": "payload.ip", "op": "in_cidr", "value": "nope"},
                {"field": "title", "op": "equals", "value": "  "}):
        with pytest.raises(ValidationError):
            Condition(**bad)


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    emitted = []
    monkeypatch.setattr(alerts_api, "emit_event", lambda tenant_id, ev, **kw: emitted.append(kw["alert_id"]))
    state = {"role": "analyst"}
    async with AsyncSessionLocal() as db:
        t, t2 = Tenant(name="sup", slug=f"sup-{h}-a"), Tenant(name="sup", slug=f"sup-{h}-b")
        user = User(email=f"sup-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                    is_active=True)
        db.add_all([t, t2, user])
        await db.flush()
        key = f"sup-{secrets.token_hex(16)}"
        wh = Webhook(tenant_id=t.id, name="Okta", key_hash=hash_webhook_key(key), key_prefix=webhook_key_prefix(key))
        foreign = SuppressionRule(tenant_id=t2.id, name="other tenant", conditions=[c("source", "equals", "okta")])
        db.add_all([wh, foreign])
        await db.commit()
        tid, t2id, wid, uid, foreign_id = t.id, t2.id, wh.id, user.id, foreign.id

    def deny():
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
    app.dependency_overrides[deps.require_analyst_or_above] = lambda: user if state["role"] != "viewer" else deny()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as cl:
            ingest = lambda ext, title, ip: cl.post("/api/v1/alerts/webhook", headers={"X-API-Key": key}, json={  # noqa: E731
                "external_id": f"{ext}-{h}", "title": title, "payload": {"ip": ip}})
            first = (await ingest("e1", "Impossible travel", "10.1.2.3")).json()
            assert first["status"] == "pending"
            assert (await cl.get(f"/api/v1/alerts/{first['id']}")).json()["title"] == "Impossible travel"

            conds = [c("title", "contains", "travel"), c("payload.ip", "in_cidr", "10.0.0.0/8")]
            p = (await cl.post("/api/v1/suppressions/preview", json={"conditions": conds})).json()
            assert p["matched"] == 1 and p["sample"][0]["id"] == first["id"], p

            r = await cl.post("/api/v1/suppressions/", json={"name": "VPN travel", "conditions": conds,
                                                             "created_from_alert_id": first["id"]})
            assert r.status_code == 201, r.text
            rule_id = r.json()["id"]

            hit = (await ingest("e2", "Impossible travel", "10.9.9.9")).json()
            assert hit["status"] == "dismissed" and hit["suppression_rule_id"] == rule_id, hit
            assert f"#{rule_id}" in hit["dismiss_reason"]
            miss = (await ingest("e3", "Impossible travel", "8.8.8.8")).json()
            assert miss["status"] == "pending"
            assert emitted == [first["id"], miss["id"]]  # the suppressed alert started nothing

            rules = (await cl.get("/api/v1/suppressions/")).json()
            assert [x["id"] for x in rules] == [rule_id]  # the other tenant's rule is invisible
            assert rules[0]["hit_count"] == 1

            # a used rule: no delete, no re-scope; disable is fine
            assert (await cl.delete(f"/api/v1/suppressions/{rule_id}")).status_code == 409
            r = await cl.put(f"/api/v1/suppressions/{rule_id}", json={"conditions": [c("title", "equals", "x")]})
            assert r.status_code == 409, r.text
            assert (await cl.put(f"/api/v1/suppressions/{rule_id}", json={"enabled": False})).json()["enabled"] is False
            assert (await ingest("e4", "Impossible travel", "10.1.1.1")).json()["status"] == "pending"

            # expired rules don't apply; unused rules can be deleted
            past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            r = await cl.post("/api/v1/suppressions/", json={"name": "old", "conditions": conds, "expires_at": past})
            assert (await ingest("e5", "Impossible travel", "10.1.1.2")).json()["status"] == "pending"
            assert (await cl.delete(f"/api/v1/suppressions/{r.json()['id']}")).status_code == 204

            # tenant isolation and roles
            assert (await cl.put(f"/api/v1/suppressions/{foreign_id}", json={"enabled": False})).status_code == 404
            assert (await cl.post("/api/v1/suppressions/", json={"name": "x", "conditions": conds,
                                                                 "created_from_alert_id": 0})).status_code == 404
            state["role"] = "viewer"
            assert (await cl.post("/api/v1/suppressions/", json={"name": "v", "conditions": conds})).status_code == 403

        async with AsyncSessionLocal() as db:
            acts = set((await db.execute(select(AuditLog.entity_type, AuditLog.action).where(
                AuditLog.tenant_id == tid))).all())
            assert {("suppression_rule", "create"), ("suppression_rule", "update"), ("suppression_rule", "delete"),
                    ("alert", "alert_suppressed")} <= acts, acts
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_([tid, t2id])))
            await db.execute(delete(Alert).where(Alert.tenant_id == tid))
            await db.execute(delete(SuppressionRule).where(SuppressionRule.tenant_id.in_([tid, t2id])))
            await db.execute(delete(Webhook).where(Webhook.id == wid))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id.in_([tid, t2id])))
            await db.commit()
        await engine.dispose()


def test_suppression_api_and_ingest(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
