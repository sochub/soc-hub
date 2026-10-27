"""Containment guardrails: protected targets, daily and per-case caps, ledger + case notes, refusals that survive
the step rollback, dry runs that record nothing, release, policy API roles/validation, and every response template
guarded."""
import asyncio
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.containment import ContainmentAction, ContainmentPolicy
from app.models.tenant import Tenant
from app.models.user import User
from app.services.containment import protected_match
from app.workflows.nodes import NodeContext, NodeError
from app.workflows.nodes.containment import run_containment_guard

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "workflows" / "templates"
PROTECTED = [{"kind": "user", "pattern": "ceo@*", "note": "VIP"}, {"kind": "host", "pattern": "dc-*"},
             {"kind": "ip", "pattern": "10.0.0.0/24"}, {"kind": "any", "pattern": "*-prod-db"}]


def test_protected_match():
    assert protected_match("user", "CEO@acme.com", PROTECTED)["note"] == "VIP"
    assert protected_match("host", "DC-01", PROTECTED) and not protected_match("host", "ws-01", PROTECTED)
    assert protected_match("ip", "10.0.0.7", PROTECTED) and not protected_match("ip", "10.0.1.7", PROTECTED)
    assert protected_match("host", "pay-prod-db", PROTECTED) and protected_match("user", "x-prod-db", PROTECTED)
    # ranges that overlap a protected range, mapped IPv6 and junk are all refused
    assert protected_match("ip", "10.0.0.0/8", PROTECTED) and protected_match("ip", "::ffff:10.0.0.5", PROTECTED)
    assert protected_match("ip", "not-an-ip", PROTECTED) and protected_match("ip", "not-an-ip", [])
    assert not protected_match("ip", "203.0.113.0/24", PROTECTED)


def test_every_response_template_is_guarded():
    for p in TEMPLATES.glob("*.json"):
        d = json.loads(p.read_text())
        if d.get("kind") != "response":
            continue
        g = d["graph"]
        guard = [n for n in g["nodes"] if n["type"] == "containment_guard"]
        assert len(guard) == 1, p.name
        # every outbound call that changes something sits downstream of the guard
        down, frontier = set(), ["guard"]
        while frontier:
            nid = frontier.pop()
            for e in g["edges"]:
                if e["source"] == nid and e["target"] not in down:
                    down.add(e["target"])
                    frontier.append(e["target"])
        writes = [n["id"] for n in g["nodes"] if n["type"] == "http_request" and n["config"]["method"] != "GET"]
        assert writes and set(writes) <= down, (p.name, writes)


async def _scenario():
    await engine.dispose()
    h = secrets.token_hex(3)
    async with AsyncSessionLocal() as db:
        t = Tenant(name="ct", slug=f"ct-{h}")
        user = User(email=f"ct-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
        db.add_all([t, user])
        await db.flush()
        c1, c2 = Case(title="c1", tenant_id=t.id), Case(title="c2", tenant_id=t.id)
        db.add_all([c1, c2, ContainmentPolicy(tenant_id=t.id, daily_cap=4, max_targets_per_case=2, protected=PROTECTED)])
        await db.commit()
        tid, uid, cid1, cid2 = t.id, user.id, c1.id, c2.id

    async def guard(case_id, kind, target, dry=False):
        async with AsyncSessionLocal() as db:
            run = SimpleNamespace(id=-1, tenant_id=tid, case_id=case_id, workflow_id=None, is_dry_run=dry)
            nctx = NodeContext(db=db, run=run, step=None, node={}, ctx={})
            try:
                out = await run_containment_guard(nctx, {"action": "Contain host", "target_kind": kind, "target": target,
                                                         "release_hint": "lift it in the console"})
                await db.commit()
                return out
            except NodeError as e:
                await db.rollback()  # what the runtime does with a failed step
                return str(e)

    def deny():
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    role = {"v": "analyst"}
    try:
        assert (await guard(cid1, "host", "ws-01"))["allowed"] is True
        assert (await guard(cid1, "host", "ws-01"))["allowed"] is True  # same target again: no new slot
        assert "protected" in await guard(cid1, "host", "DC-01")
        assert (await guard(cid1, "host", "ws-02"))["allowed"] is True
        assert "already has 2 contained targets" in await guard(cid1, "host", "ws-03")
        assert "dry" not in str(await guard(cid2, "user", "bob@acme.com", dry=True))
        assert (await guard(cid2, "user", "bob@acme.com"))["allowed"] is True  # 4th action today
        assert "daily cap of 4" in await guard(cid2, "ip", "203.0.113.9")
        assert "empty" in await guard(cid2, "ip", "  ")
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(ContainmentAction.target, ContainmentAction.status).where(
                ContainmentAction.tenant_id == tid).order_by(ContainmentAction.id))).all()
            assert rows == [("ws-01", "active"), ("ws-01", "active"), ("DC-01", "blocked"), ("ws-02", "active"),
                            ("ws-03", "blocked"), ("bob@acme.com", "active"), ("203.0.113.9", "blocked")], rows
            notes = (await db.execute(select(TimelineEvent.event_type).where(TimelineEvent.case_id == cid1))).scalars().all()
            assert notes.count("containment") == 3 and notes.count("containment_blocked") == 2, notes

        app.dependency_overrides[deps.get_current_active_user] = lambda: user
        app.dependency_overrides[deps.get_effective_tenant_id] = lambda: tid
        app.dependency_overrides[deps.require_analyst_or_above] = lambda: user if role["v"] != "viewer" else deny()
        app.dependency_overrides[deps.require_admin] = lambda: user if role["v"] == "admin" else deny()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            acts = (await c.get(f"/api/v1/containment/actions?case_id={cid1}&status=active")).json()
            assert {a["target"] for a in acts} == {"ws-01", "ws-02"} and acts[0]["release_hint"]
            r = await c.post(f"/api/v1/containment/actions/{acts[0]['id']}/release")
            assert r.status_code == 200 and r.json()["status"] == "released", r.text
            assert (await c.post(f"/api/v1/containment/actions/{acts[0]['id']}/release")).status_code == 409
            pol = {"enabled": True, "daily_cap": 10, "max_targets_per_case": 3,
                   "protected": [{"kind": "ip", "pattern": "192.168.0.0/16"}]}
            assert (await c.put("/api/v1/containment/policy", json=pol)).status_code == 403  # analysts can't
            role["v"] = "admin"
            bad = {**pol, "protected": [{"kind": "ip", "pattern": "nope"}]}
            assert (await c.put("/api/v1/containment/policy", json=bad)).status_code == 422
            r = await c.put("/api/v1/containment/policy", json=pol)
            assert r.status_code == 200 and r.json()["daily_cap"] == 10, r.text
            role["v"] = "viewer"
            assert (await c.post(f"/api/v1/containment/actions/{acts[1]['id']}/release")).status_code == 403
        # releasing freed a slot on case 1
        assert (await guard(cid1, "host", "ws-03"))["allowed"] is True
        async with AsyncSessionLocal() as db:
            acts = set((await db.execute(select(AuditLog.action).where(AuditLog.tenant_id == tid))).scalars())
            assert {"contained", "blocked", "released", "update"} <= acts, acts
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
            await db.execute(delete(ContainmentAction).where(ContainmentAction.tenant_id == tid))
            await db.execute(delete(ContainmentPolicy).where(ContainmentPolicy.tenant_id == tid))
            await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_([cid1, cid2])))
            await db.execute(delete(Case).where(Case.id.in_([cid1, cid2])))
            await db.execute(delete(User).where(User.id == uid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()


def test_containment_guard_flow():
    asyncio.run(_scenario())


@pytest.mark.parametrize("cfg", [{"action": "x", "target_kind": "printer", "target": "p1"}])
def test_guard_rejects_unknown_kind(cfg):
    nctx = NodeContext(db=None, run=SimpleNamespace(), step=None, node={}, ctx={})
    with pytest.raises(NodeError):
        asyncio.run(run_containment_guard(nctx, cfg))
