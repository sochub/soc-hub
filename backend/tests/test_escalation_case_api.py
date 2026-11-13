import asyncio
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
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseSeverity, TimelineEvent
from app.models.escalation import CaseEscalation, EscalationTier, EscalationTierGroup, UserGroup, UserGroupMember
from app.models.membership import TenantMembership
from app.models.notification import Notification
from app.models.tenant import Tenant
from app.models.user import User
from app.services.case_service import apply_case_update

TEXTS = dict(reason="Lateral movement", summary="Seen on 3 hosts", requested_help="Contain and scope")


def _deny():
    raise HTTPException(status_code=403, detail="Insufficient permissions")


async def _scenario():
    h = pysecrets.token_hex(3)
    ids = {"tenants": [], "users": []}
    state = {"user": None}
    await engine.dispose()
    async with AsyncSessionLocal() as db:
        try:
            ta, tx = Tenant(name="esc-api", slug=f"esc-api-{h}-a"), Tenant(name="esc-api", slug=f"esc-api-{h}-x")
            db.add_all([ta, tx])
            await db.flush()
            ids["tenants"] += [ta.id, tx.id]
            T = ta.id
            us = {n: User(email=f"{n}-{h}@example.test", full_name=n.upper(), is_active=True,
                          hashed_password=get_password_hash(pysecrets.token_hex(8)))
                  for n in ("l1", "l2", "adm", "view", "x")}
            db.add_all(us.values())
            await db.flush()
            ids["users"] += [u.id for u in us.values()]
            roles = {"l1": "analyst", "l2": "analyst", "adm": "admin", "view": "viewer"}
            ms = {n: TenantMembership(user_id=us[n].id, tenant_id=T, role=r) for n, r in roles.items()}
            db.add_all(ms.values())
            db.add(TenantMembership(user_id=us["x"].id, tenant_id=tx.id, role="analyst"))
            g1, g2, g3 = (UserGroup(tenant_id=T, name=n) for n in ("G1", "G2", "G3"))
            db.add_all([g1, g2, g3])
            await db.flush()
            db.add_all([UserGroupMember(group_id=g1.id, membership_id=ms["l1"].id),
                        UserGroupMember(group_id=g2.id, membership_id=ms["l2"].id)])
            t1, t2 = (EscalationTier(tenant_id=T, position=1, name="Tier 1"),
                      EscalationTier(tenant_id=T, position=2, name="Tier 2"))
            db.add_all([t1, t2])
            await db.flush()
            for t, g in ((t1, g1), (t2, g2), (t2, g3)):
                db.add(EscalationTierGroup(tier_id=t.id, group_id=g.id))
            mk = lambda title, sev, **kw: Case(title=title, tenant_id=T, severity=sev, owner_id=us["l1"].id, **kw)
            c = [mk(f"c{i}", CaseSeverity.LOW) for i in range(6)]
            c[1].severity = CaseSeverity.CRITICAL
            cx = Case(title="foreign", tenant_id=tx.id)
            db.add_all(c + [cx])
            await db.flush()
            await db.commit()
            U = {n: u.id for n, u in us.items()}
            C = [x.id for x in c]
            cx_id, t2_id, g2_id, g3_id = cx.id, t2.id, g2.id, g3.id

            async def as_user(name):
                async with AsyncSessionLocal() as s:
                    u = (await s.execute(select(User).where(User.id == U[name])
                                         .options(selectinload(User.memberships)))).scalar_one()
                u._active_tenant_id = T
                state["user"] = u
            await as_user("l1")
            app.dependency_overrides[deps.get_current_active_user] = lambda: state["user"]
            app.dependency_overrides[deps.get_effective_tenant_id] = lambda: T
            app.dependency_overrides[deps.require_analyst_or_above] = (
                lambda: state["user"] if state["user"].id != U["view"] else _deny())
            body = dict(to_tier_id=t2_id, to_group_id=g2_id, **TEXTS)
            E = "/api/v1/escalations"

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
                async def statuses(case_id):
                    return [(r["status"], r["accepted_via"]) for r in (await http.get(f"{E}/cases/{case_id}/history")).json()]

                # permissions + tenancy
                await as_user("view")
                assert (await http.post(f"{E}/cases/{C[0]}/escalate", json=body)).status_code == 403, "viewer"
                await as_user("l1")
                assert (await http.post(f"{E}/cases/{cx_id}/escalate", json=body)).status_code == 404, "foreign case"
                assert (await http.get(f"{E}/cases/{cx_id}/history")).status_code == 404, "foreign history"
                assert (await http.post(f"{E}/cases/{C[0]}/escalate", json={**body, "reason": ""})).status_code == 422

                # escalate -> case read embeds escalation (detail + list)
                r = await http.post(f"{E}/cases/{C[0]}/escalate", json=body)
                assert r.status_code == 200, r.text
                assert r.json()["status"] == "pending" and r.json()["group_name"] == "G2", r.json()
                r = await http.get(f"/api/v1/cases/{C[0]}")
                esc = r.json()["escalation"]
                assert esc["status"] == "pending" and esc["tier_position"] == 2 and esc["tier_name"] == "Tier 2"
                assert esc["group_id"] == g2_id and esc["requested_by_name"] == "L1" and esc["accepted_by_name"] is None
                assert set(esc) == {"id", "status", "tier_position", "tier_name", "group_id", "group_name",
                                    "requested_by_name", "requested_at", "due_at", "accepted_by_name", "accepted_at"}
                listed = {x["id"]: x for x in (await http.get("/api/v1/cases/", params={"limit": 500})).json()}
                assert listed[C[0]]["escalation"]["status"] == "pending" and listed[C[1]]["escalation"] is None

                # failed supersede rolls back: stale tier leaves the pending row untouched
                r = await http.post(f"{E}/cases/{C[0]}/escalate", json={**body, "to_tier_id": 10**9})
                assert r.status_code == 422, r.text
                assert await statuses(C[0]) == [("pending", None)], "rollback left history intact"

                # queue: only the caller's groups; severity first
                for cid in (C[1], C[2]):
                    assert (await http.post(f"{E}/cases/{cid}/escalate", json=body)).status_code == 200
                assert (await http.post(f"{E}/cases/{C[3]}/escalate", json={**body, "to_group_id": g3_id})).status_code == 200
                assert (await http.get(f"{E}/queue")).json() == [], "l1 is in no tier-2 group"
                await as_user("l2")
                q = (await http.get(f"{E}/queue")).json()
                assert [x["case_id"] for x in q] == [C[1], C[0], C[2]], [x["case_id"] for x in q]
                assert q[0]["severity"] == "critical" and q[0]["group_name"] == "G2" and q[0]["requested_by_name"] == "L1"
                assert {"id", "case_title", "tier_name", "created_at", "due_at"} <= set(q[0])

                # accept: group member ok; non-member analyst 403; admin override
                await as_user("l1")
                assert (await http.post(f"{E}/cases/{C[0]}/accept")).status_code == 403
                await as_user("l2")
                r = await http.post(f"{E}/cases/{C[0]}/accept")
                assert r.status_code == 200 and r.json()["accepted_via"] == "soc_hub", r.text
                assert (await http.get(f"/api/v1/cases/{C[0]}")).json()["escalation"]["accepted_by_name"] == "L2"
                assert (await http.post(f"{E}/cases/{C[0]}/accept")).status_code == 409
                await as_user("adm")
                r = await http.post(f"{E}/cases/{C[3]}/accept")
                assert r.json()["accepted_via"] == "admin_override", r.text

                # redirect / return / withdraw
                await as_user("l1")
                r = await http.post(f"{E}/cases/{C[2]}/redirect", json={"to_group_id": g3_id, "note": "wrong team"})
                assert r.status_code == 200 and r.json()["group_name"] == "G3", r.text
                assert (await http.post(f"{E}/cases/{C[2]}/redirect", json={"to_group_id": g3_id, "note": ""})).status_code == 422
                r = await http.post(f"{E}/cases/{C[2]}/withdraw", json={})
                assert r.status_code == 200 and r.json()["status"] == "withdrawn"
                assert (await http.get(f"/api/v1/cases/{C[2]}")).json()["escalation"] is None
                r = await http.post(f"{E}/cases/{C[0]}/return", json={"note": "need host timeline"})
                assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["tier_position"] == 1, r.text
                hist = (await http.get(f"{E}/cases/{C[0]}/history")).json()
                assert [x["status"] for x in hist] == ["pending", "returned"], hist
                assert hist[1]["close_note"] == "need host timeline" and hist[1]["closed_by_name"] == "L1"

                # PATCH-equivalent: PUT owner while pending -> accepted via assignment
                r = await http.put(f"/api/v1/cases/{C[1]}", json={"owner_id": U["l2"]})
                assert r.status_code == 200, r.text
                assert await statuses(C[1]) == [("accepted", "assignment")]
                # closing with a pending escalation -> cancelled
                assert (await http.post(f"{E}/cases/{C[4]}/escalate", json=body)).status_code == 200
                r = await http.put(f"/api/v1/cases/{C[4]}", json={"status": "resolved"})
                assert r.status_code == 200, r.text
                assert await statuses(C[4]) == [("cancelled", None)]
                assert r.json()["escalation"] is None

            # Workflow/Slack path: apply_case_update directly
            async with AsyncSessionLocal() as s:
                case = await s.get(Case, C[5])
                await apply_case_update(s, case=case, update_data={"owner_id": None}, user_id=None)  # no-op
                await s.commit()
            async with AsyncSessionLocal() as s:
                from app.escalations import service as esc_svc
                case = await s.get(Case, C[5])
                await esc_svc.escalate(s, case=case, actor_id=U["l1"], to_tier_id=t2_id, to_group_id=g2_id, **TEXTS)
                await apply_case_update(s, case=case, update_data={"owner_id": U["adm"]}, user_id=None)
                await s.commit()
                row = await esc_svc.current_row(s, C[5])
                assert row.status == "accepted" and row.accepted_via == "assignment" and row.accepted_by == U["adm"]
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


def test_escalation_case_api(monkeypatch):
    monkeypatch.setattr(wf_events, "emit_event", lambda *a, **k: None)
    monkeypatch.setattr(delivery, "_enqueue", lambda ids: None)
    asyncio.run(_scenario())
