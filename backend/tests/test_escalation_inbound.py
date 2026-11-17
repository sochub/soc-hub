"""Inbound PagerDuty/Opsgenie acks: the public webhook trust boundary, and accept_external idempotency."""
import asyncio
import hashlib
import hmac
import json
import secrets as pysecrets

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

import app.notifications.delivery as delivery
import app.workflows.events as wf_events
import app.main  # noqa: F401  (registers every model mapper)
from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.escalations import inbound
from app.escalations import service as svc
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.escalation import (
    CaseEscalation, EscalationIntegration, EscalationTier, EscalationTierGroup, UserGroup, UserGroupMember,
)
from app.models.membership import TenantMembership
from app.models.notification import Notification
from app.models.tenant import Tenant
from app.models.user import User
from app.tasks import escalations as esc_tasks
from app.utils.crypto import decrypt, encrypt

TEXTS = dict(reason="Lateral movement", summary="Seen on 3 hosts", requested_help="Contain and scope")
PD_SECRET, OG_TOKEN = "pd-webhook-secret-xyz", "og-token-abc"


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


def _sig(raw: bytes, secret: str = PD_SECRET) -> str:
    return "v1=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _pd_event(event_type="incident.acknowledged", incident_key="sochub-case-1-esc-2", agent="PUSER01",
              incident_id="PINC001") -> bytes:
    data = {"id": incident_id, "type": "incident"}
    if incident_key is not None:
        data["incident_key"] = incident_key
    return json.dumps({"event": {"id": "E1", "event_type": event_type,
                                 "agent": {"id": agent, "type": "user_reference"}, "data": data}}).encode()


class FakeRedis:
    def __init__(self):
        self.store, self.flood, self.broken = {}, False, False

    async def incr(self, key):
        if self.broken:
            raise ConnectionError("down")
        self.store[key] = 121 if self.flood else self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, *a, **k):
        return True

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def aclose(self):
        pass


async def _scenario(enqueued, pd_calls, fake_redis):
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": []}
    state = {"user": None}
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            ta = Tenant(name="esc-in", slug=f"esc-in-{h}-a")
            tb = Tenant(name="esc-in", slug=f"esc-in-{h}-b")
            td = Tenant(name="esc-in", slug=f"esc-in-{h}-d", is_active=False)
            db.add_all([ta, tb, td])
            await db.flush()
            ids["tenants"] += [ta.id, tb.id, td.id]
            T = ta.id
            us = {n: User(email=f"{n}-{h}@example.test", full_name=n.upper(), is_active=True,
                          hashed_password=get_password_hash(pysecrets.token_hex(8)))
                  for n in ("l1", "mem", "non", "adm")}
            db.add_all(us.values())
            await db.flush()
            ids["users"] += [u.id for u in us.values()]
            ms = {n: TenantMembership(user_id=us[n].id, tenant_id=T, role=r)
                  for n, r in (("l1", "analyst"), ("mem", "analyst"), ("non", "analyst"), ("adm", "admin"))}
            db.add_all(ms.values())
            g1, g2 = UserGroup(tenant_id=T, name="SOC L1"), UserGroup(tenant_id=T, name="SOC L2")
            db.add_all([g1, g2])
            await db.flush()
            db.add_all([UserGroupMember(group_id=g1.id, membership_id=ms["l1"].id),
                        UserGroupMember(group_id=g2.id, membership_id=ms["mem"].id)])
            t1 = EscalationTier(tenant_id=T, position=1, name="Tier 1")
            t2 = EscalationTier(tenant_id=T, position=2, name="Tier 2")
            db.add_all([t1, t2])
            await db.flush()
            db.add_all([EscalationTierGroup(tier_id=t1.id, group_id=g1.id),
                        EscalationTierGroup(tier_id=t2.id, group_id=g2.id)])
            cases = [Case(title=f"c{i}", tenant_id=T, owner_id=us["l1"].id) for i in range(4)]
            db.add_all(cases)
            await db.flush()
            rows = []
            for c in cases:
                rows.append(await svc.escalate(db, case=c, actor_id=us["l1"].id, to_tier_id=t2.id,
                                               to_group_id=g2.id, **TEXTS))
            key_a, key_b, key_d = (pysecrets.token_urlsafe(32) for _ in range(3))
            db.add_all([
                EscalationIntegration(tenant_id=T, inbound_key=key_a, pagerduty_webhook_secret_enc=encrypt(PD_SECRET),
                                      pagerduty_api_key_enc=encrypt("pd-api-key"), opsgenie_token_enc=encrypt(OG_TOKEN)),
                EscalationIntegration(tenant_id=tb.id, inbound_key=key_b),  # nothing configured
                EscalationIntegration(tenant_id=td.id, inbound_key=key_d,
                                      pagerduty_webhook_secret_enc=encrypt(PD_SECRET),
                                      opsgenie_token_enc=encrypt(OG_TOKEN)),
            ])
            await db.commit()
            U = {n: u.id for n, u in us.items()}
            C = [c.id for c in cases]
            R = [r.id for r in rows]
            DK = [f"sochub-case-{c}-esc-{r}" for c, r in zip(C, R)]
            TB = tb.id

            async with AsyncSessionLocal() as s:
                adm = (await s.execute(select(User).where(User.id == U["adm"])
                                       .options(selectinload(User.memberships)))).scalar_one()
            adm._active_tenant_id = T
            state["user"] = adm
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: T
            app.dependency_overrides[deps.require_admin] = (
                lambda: state["user"] if state["user"].id == U["adm"] else _deny())
            app.dependency_overrides[inbound.get_inbound_redis] = lambda: fake_redis
            I = "/api/v1/escalations/inbound"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
                # ── PagerDuty ────────────────────────────────────────────
                pd = f"{I}/{key_a}/pagerduty"
                raw = _pd_event(incident_key=DK[0], agent="PUSER01")
                r = await http.post(pd, content=raw, headers={"X-PagerDuty-Signature": _sig(raw, "wrong")})
                assert r.status_code == 401, r.text
                r = await http.post(pd, content=raw)
                assert r.status_code == 401, "missing signature"
                assert enqueued == []
                # any of several signatures may match (secret rotation)
                r = await http.post(pd, content=raw, headers={"X-PagerDuty-Signature": f"{_sig(raw, 'old')},{_sig(raw)}"})
                assert r.status_code == 202, r.text
                assert enqueued == [((T, "pagerduty", DK[0], "PUSER01"), {})], enqueued
                enqueued.clear()
                # signature is over the exact bytes: re-serialised JSON fails
                r = await http.post(pd, content=raw + b" ", headers={"X-PagerDuty-Signature": _sig(raw)})
                assert r.status_code == 401
                # non-ack events: 202, nothing enqueued
                raw2 = _pd_event(event_type="incident.triggered", incident_key=DK[0])
                r = await http.post(pd, content=raw2, headers={"X-PagerDuty-Signature": _sig(raw2)})
                assert r.status_code == 202 and enqueued == []
                # foreign dedup keys are not ours: 202, nothing enqueued
                raw3 = _pd_event(incident_key="some-other-incident")
                r = await http.post(pd, content=raw3, headers={"X-PagerDuty-Signature": _sig(raw3)})
                assert r.status_code == 202 and enqueued == []
                # no incident_key: enqueue with the incident id for a REST lookup
                raw4 = _pd_event(incident_key=None, incident_id="PINC9")
                r = await http.post(pd, content=raw4, headers={"X-PagerDuty-Signature": _sig(raw4)})
                assert r.status_code == 202 and enqueued == [((T, "pagerduty", None, "PUSER01"),
                                                             {"incident_id": "PINC9"})], enqueued
                enqueued.clear()
                # malformed JSON after a valid signature -> 400
                bad = b"{not json"
                r = await http.post(pd, content=bad, headers={"X-PagerDuty-Signature": _sig(bad)})
                assert r.status_code == 400, r.text
                # body cap
                big = b"x" * (64 * 1024 + 1)
                r = await http.post(pd, content=big, headers={"X-PagerDuty-Signature": _sig(big)})
                assert r.status_code == 413, r.text
                # unknown key, deactivated tenant, nothing configured: identical 404s
                for url in (f"{I}/{pysecrets.token_urlsafe(32)}/pagerduty", f"{I}/{key_d}/pagerduty",
                            f"{I}/{key_b}/pagerduty", f"{I}/{key_d}/opsgenie", f"{I}/{key_b}/opsgenie"):
                    r = await http.post(url, content=raw, headers={"X-PagerDuty-Signature": _sig(raw),
                                                                   "X-SocHub-Token": OG_TOKEN})
                    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, (url, r.text)
                assert enqueued == []
                # rate limit: 429 when the window is full; fails open when Redis is down
                fake_redis.flood = True
                r = await http.post(pd, content=raw, headers={"X-PagerDuty-Signature": _sig(raw)})
                assert r.status_code == 429 and enqueued == []
                fake_redis.flood, fake_redis.broken = False, True
                fake_redis.store.clear()
                r = await http.post(pd, content=raw, headers={"X-PagerDuty-Signature": _sig(raw)})
                assert r.status_code == 202
                fake_redis.broken = False
                enqueued.clear()

                # ── Opsgenie ─────────────────────────────────────────────
                og = f"{I}/{key_a}/opsgenie"
                body = {"action": "Acknowledge", "alert": {"alias": DK[1], "username": f"mem-{h}@example.test"}}
                assert (await http.post(og, json=body, headers={"X-SocHub-Token": "nope"})).status_code == 401
                assert (await http.post(og, json=body)).status_code == 401
                assert enqueued == []
                r = await http.post(og, json=body, headers={"X-SocHub-Token": OG_TOKEN})
                assert r.status_code == 202, r.text
                assert enqueued == [((T, "opsgenie", DK[1], f"mem-{h}@example.test"), {})], enqueued
                enqueued.clear()
                r = await http.post(og, json={**body, "action": "Close"}, headers={"X-SocHub-Token": OG_TOKEN})
                assert r.status_code == 202 and enqueued == []
                r = await http.post(og, content=b"x" * (64 * 1024 + 1), headers={"X-SocHub-Token": OG_TOKEN})
                assert r.status_code == 413

                # ── admin integration config ─────────────────────────────
                E = "/api/v1/escalations/integrations"
                async with AsyncSessionLocal() as s:  # tenant A row already exists; config reads it
                    await s.execute(delete(EscalationIntegration).where(EscalationIntegration.tenant_id == T))
                    await s.commit()
                r = await http.get(E)
                assert r.status_code == 200, r.text
                cfg = r.json()
                async with AsyncSessionLocal() as s:
                    integ = (await s.execute(select(EscalationIntegration).where(
                        EscalationIntegration.tenant_id == T))).scalar_one()
                key_new = integ.inbound_key
                assert cfg == {"pagerduty_url": f"https://soc.example.com/api/v1/escalations/inbound/{key_new}/pagerduty",
                               "opsgenie_url": f"https://soc.example.com/api/v1/escalations/inbound/{key_new}/opsgenie",
                               "has_pagerduty_webhook_secret": False, "has_pagerduty_api_key": False,
                               "has_opsgenie_token": False}, cfg
                assert (await http.get(E)).json() == cfg, "second GET reuses the row"
                r = await http.put(E, json={"pagerduty_webhook_secret": "s3cret", "pagerduty_api_key": "k3y"})
                assert r.status_code == 200 and r.json()["has_pagerduty_webhook_secret"] and r.json()["has_pagerduty_api_key"]
                assert "s3cret" not in r.text and "k3y" not in r.text
                r = await http.put(E, json={"pagerduty_api_key": ""})
                assert r.json()["has_pagerduty_webhook_secret"] and not r.json()["has_pagerduty_api_key"]
                r = await http.post(f"{E}/opsgenie-token")
                assert r.status_code == 200, r.text
                tok = r.json()["token"]
                assert len(tok) >= 32 and (await http.get(E)).json()["has_opsgenie_token"]
                async with AsyncSessionLocal() as s:
                    integ = (await s.execute(select(EscalationIntegration).where(
                        EscalationIntegration.tenant_id == T))).scalar_one()
                    assert integ.pagerduty_webhook_secret_enc != "s3cret" and decrypt(integ.pagerduty_webhook_secret_enc) == "s3cret"
                    assert decrypt(integ.opsgenie_token_enc) == tok
                    audit = (await s.execute(select(AuditLog).where(AuditLog.tenant_id == T, AuditLog.action.like(
                        "escalation_integration%")))).scalars().all()
                    assert audit and all("s3cret" not in json.dumps(a.changes) and tok not in json.dumps(a.changes)
                                         for a in audit)
                # the freshly generated token works on the new URL
                r = await http.post(f"{I}/{key_new}/opsgenie", json=body, headers={"X-SocHub-Token": tok})
                assert r.status_code == 202 and len(enqueued) == 1
                enqueued.clear()
                state["user"] = User(id=U["non"], email="x", is_active=True)
                assert (await http.get(E)).status_code == 403
                assert (await http.put(E, json={})).status_code == 403
                assert (await http.post(f"{E}/opsgenie-token")).status_code == 403
                state["user"] = adm

            # ── accept_external ──────────────────────────────────────────
            async def accept_ext(dk, email, via="pagerduty", tenant=T, actor_ref=None):
                async with AsyncSessionLocal() as s:
                    out = await svc.accept_external(s, tenant_id=tenant, dedup_key=dk, email=email, via=via,
                                                    actor_ref=actor_ref)
                    await s.commit()
                    return out

            async def timeline(cid):
                async with AsyncSessionLocal() as s:
                    return (await s.execute(select(TimelineEvent.content).where(
                        TimelineEvent.case_id == cid).order_by(TimelineEvent.id))).scalars().all()

            async def row(rid):
                async with AsyncSessionLocal() as s:
                    return await s.get(CaseEscalation, rid)

            # malformed / foreign / wrong tenant / mismatched keys are ignored
            for dk in ("garbage", f"sochub-case-{C[0]}-esc-{R[0]}\n", f"sochub-case-{C[0]}-esc-{R[1]}",
                       f"sochub-case-{C[0]}-esc-99999999999999999999", f" sochub-case-{C[0]}-esc-{R[0]}",
                       f"sochub-case-{C[0]}-esc-" + "".join(chr(0x0660 + int(d)) for d in str(R[0]))):
                assert await accept_ext(dk, f"mem-{h}@example.test") == "ignored", dk
            assert await accept_ext(DK[0], f"mem-{h}@example.test", tenant=TB) == "ignored"
            assert (await row(R[0])).status == "pending"

            # non-member -> unmatched, stays pending, timeline + audit (deduped on redelivery)
            assert await accept_ext(DK[0], f"non-{h}@example.test") == "unmatched"
            assert await accept_ext(DK[0], f"non-{h}@example.test") == "unmatched"
            r0 = await row(R[0])
            assert r0.status == "pending" and r0.accepted_by is None
            tl = await timeline(C[0])
            expect = f"Acknowledged in PagerDuty by non-{h}@example.test (no matching member of SOC L2)"
            assert tl.count(expect) == 1, tl
            # unknown agent (PagerDuty lookup failed): logged with the agent id
            assert await accept_ext(DK[0], None, actor_ref="PUSER77") == "unmatched"
            assert any("PUSER77" in t and "no matching member of SOC L2" in t for t in await timeline(C[0]))
            async with AsyncSessionLocal() as s:
                aud = (await s.execute(select(AuditLog).where(
                    AuditLog.tenant_id == T, AuditLog.entity_id == C[0],
                    AuditLog.action == "case_escalation_external_ack_unmatched"))).scalars().all()
                assert len(aud) == 2, [a.changes for a in aud]

            # member (case-insensitive email) -> accepted via PagerDuty; repeats and late acks are ignored
            assert await accept_ext(DK[0], f"MEM-{h}@Example.TEST") == "accepted"
            assert await accept_ext(DK[0], f"mem-{h}@example.test") == "ignored"
            r0 = await row(R[0])
            assert r0.status == "accepted" and r0.accepted_via == "pagerduty" and r0.accepted_by == U["mem"]
            async with AsyncSessionLocal() as s:
                assert (await s.get(Case, C[0])).owner_id == U["mem"]
            tl = await timeline(C[0])
            assert [t for t in tl if t.startswith("Accepted via")] == ["Accepted via PagerDuty by MEM"], tl

            # after the row closed some other way -> ignored, no accept entry
            async with AsyncSessionLocal() as s:
                await svc.withdraw(s, case=await s.get(Case, C[1]), actor_id=U["l1"], note=None)
                await s.commit()
            assert await accept_ext(DK[1], f"mem-{h}@example.test", via="opsgenie") == "ignored"
            assert (await row(R[1])).status == "withdrawn"
            assert not [t for t in await timeline(C[1]) if t.startswith("Accepted via")]

            # concurrent duplicate deliveries -> exactly one accept
            out = await asyncio.gather(*(accept_ext(DK[2], f"mem-{h}@example.test", via="opsgenie") for _ in range(3)))
            assert sorted(out) == ["accepted", "ignored", "ignored"], out
            assert [t for t in await timeline(C[2]) if t.startswith("Accepted via")] == ["Accepted via Opsgenie by MEM"]

            # deactivated tenant: a queued ack does nothing
            async def set_active(v):
                async with AsyncSessionLocal() as s:
                    (await s.get(Tenant, T)).is_active = v
                    await s.commit()
            await set_active(False)
            assert await accept_ext(DK[3], f"mem-{h}@example.test") == "ignored"
            await set_active(True)
            assert (await row(R[3])).status == "pending"

            # ── process_ack task body: PagerDuty lookups ─────────────────
            async with AsyncSessionLocal() as s:  # the admin section above cleared the API key
                integ = (await s.execute(select(EscalationIntegration).where(
                    EscalationIntegration.tenant_id == T))).scalar_one()
                integ.pagerduty_api_key_enc = encrypt("pd-api-key")
                await s.commit()
            pd_calls["users/PUSER01"] = {"user": {"email": f"mem-{h}@example.test"}}
            pd_calls["incidents/PINC3"] = {"incident": {"incident_key": DK[3]}}
            assert await esc_tasks._process_ack(T, "pagerduty", None, "PBROKEN", incident_id="PINC3") == "unmatched"
            assert any("PBROKEN" in t for t in await timeline(C[3])), "lookup failure -> logged with agent id"
            assert await esc_tasks._process_ack(T, "pagerduty", None, "PUSER01", incident_id="PINC3") == "accepted"
            assert (await row(R[3])).accepted_via == "pagerduty"
            assert fake_redis.store.get(f"pd:user:{T}:PUSER01"), "email cached"
            pd_calls.pop("users/PUSER01")
            assert await esc_tasks._process_ack(T, "pagerduty", DK[3], "PUSER01") == "ignored"
            # deactivated tenant: the task does nothing
            assert await esc_tasks._process_ack(ids["tenants"][2], "opsgenie", DK[3], "x@example.test") == "ignored"
        finally:
            app.dependency_overrides.clear()
            await db.rollback()
            async with AsyncSessionLocal() as s3:
                cids = (await s3.execute(select(Case.id).where(Case.tenant_id.in_(ids["tenants"])))).scalars().all()
                await s3.execute(delete(Notification).where(Notification.tenant_id.in_(ids["tenants"])))
                await s3.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(cids)))
                await s3.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(ids["tenants"])))
                await s3.execute(delete(Case).where(Case.id.in_(cids)))
                await s3.execute(delete(Tenant).where(Tenant.id.in_(ids["tenants"])))
                await s3.execute(delete(User).where(User.id.in_(ids["users"])))
                await s3.commit()
    await engine.dispose()


def test_escalation_inbound(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://soc.example.com")  # don't rely on another test's leak
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://soc.example.com")
    enqueued, pd_calls, fake_redis = [], {}, FakeRedis()
    monkeypatch.setattr(wf_events, "emit_event", lambda *a, **k: None)
    monkeypatch.setattr(delivery, "_enqueue", lambda ids: None)
    monkeypatch.setattr(esc_tasks.process_ack_task, "delay", lambda *a, **k: enqueued.append((a, k)))

    async def fake_pd_get(api_key, path):
        assert api_key == "pd-api-key"
        if path not in pd_calls:
            raise httpx.ConnectError("no network in tests")
        return pd_calls[path]
    monkeypatch.setattr(esc_tasks, "_pd_get", fake_pd_get)
    monkeypatch.setattr(esc_tasks, "_redis", lambda: fake_redis)
    asyncio.run(_scenario(enqueued, pd_calls, fake_redis))
