"""Alert retention policies: preview, purge scope (age, status, conditions, tenant), case-linked alerts kept, admin only."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.case import Alert
from app.models.membership import TenantMembership
from app.models.suppression_rule import SuppressionRule
from app.services import alert_retention
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run
from tests.session_fixtures import make_web_token

UTC = timezone.utc


def test_alert_retention_policy():
    async def body(ids):
        alert_ids = []
        try:
            async with AsyncSessionLocal() as db:
                t, other = await make_tenant(db, ids), await make_tenant(db, ids)
                admin, analyst = await make_user(db, ids), await make_user(db, ids)
                db.add_all([TenantMembership(user_id=admin.id, tenant_id=t.id, role="admin"),
                            TenantMembership(user_id=analyst.id, tenant_id=t.id, role="analyst")])
                await db.commit()
                h = {"Authorization": f"Bearer {await make_web_token(db, admin, t.id)}"}
                ha = {"Authorization": f"Bearer {await make_web_token(db, analyst, t.id)}"}
                case = await make_case(db, ids, t)
                now = datetime.now(UTC)
                old, new = now - timedelta(days=120), now - timedelta(days=10)
                rows = {  # title: (tenant, source, status, age, case)
                    "old-dismissed-wazuh": (t, "wazuh", "dismissed", old, None),
                    "old-dismissed-okta": (t, "okta", "dismissed", old, None),
                    "old-pending-wazuh": (t, "wazuh", "pending", old, None),
                    "old-dismissed-on-case": (t, "wazuh", "dismissed", old, case.id),
                    "old-promoted": (t, "wazuh", "promoted", old, case.id),
                    "new-dismissed-wazuh": (t, "wazuh", "dismissed", new, None),
                    "other-tenant": (other, "wazuh", "dismissed", old, None),
                }
                by_title = {}
                for i, (title, (tenant, src, status, at, cid)) in enumerate(rows.items()):
                    a = Alert(tenant_id=tenant.id, source=src, external_id=f"ret-{tenant.id}-{i}", title=title,
                              severity="low", payload={"agent": {"name": "a1"}}, status=status, created_at=at,
                              case_id=cid)
                    db.add(a)
                    await db.flush()
                    alert_ids.append(a.id)
                    by_title[title] = a.id
                rule = SuppressionRule(tenant_id=t.id, name="r", conditions=[], created_from_alert_id=by_title["old-dismissed-wazuh"])
                db.add(rule)
                await db.commit()

            scope = {"retain_days": 90, "statuses": ["dismissed"],
                     "conditions": [{"field": "source", "op": "equals", "value": "wazuh"}]}
            async with client() as cl:
                # admin only
                assert (await cl.get("/api/v1/alert-retention/", headers=ha)).status_code == 403
                assert (await cl.post("/api/v1/alert-retention/preview", headers=ha, json=scope)).status_code == 403

                r = await cl.post("/api/v1/alert-retention/preview", headers=h, json=scope)
                assert r.status_code == 200, r.text
                p = r.json()
                assert p["matched"] == 1 and p["kept_linked_to_case"] == 1
                assert [s["title"] for s in p["sample"]] == ["old-dismissed-wazuh"]

                # bad input
                for bad in ({**scope, "statuses": ["promoted"]}, {**scope, "statuses": []}, {**scope, "retain_days": 0}):
                    assert (await cl.post("/api/v1/alert-retention/preview", headers=h, json=bad)).status_code == 422

                r = await cl.post("/api/v1/alert-retention/", headers=h, json={"name": "wazuh noise", **scope})
                assert r.status_code == 201, r.text
                pol = r.json()
                assert pol["enabled"] is False  # created disabled: preview, then enable

            # disabled policies never run
            assert await alert_retention.run_all(tenant_ids=[t.id, other.id]) == 0

            async with client() as cl:
                r = await cl.put(f"/api/v1/alert-retention/{pol['id']}", headers=h, json={"enabled": True})
                assert r.status_code == 200 and r.json()["enabled"] is True
                assert (await cl.put(f"/api/v1/alert-retention/{pol['id']}", headers=h, json={"retain_days": None})).status_code == 422

            assert await alert_retention.run_all(tenant_ids=[t.id, other.id]) == 1
            async with AsyncSessionLocal() as db:
                left = set((await db.execute(select(Alert.title).where(Alert.id.in_(alert_ids)))).scalars())
                assert left == set(rows) - {"old-dismissed-wazuh"}
                assert (await db.get(SuppressionRule, rule.id)).created_from_alert_id is None  # SET NULL, no cascade
                audit = (await db.execute(select(AuditLog).where(AuditLog.tenant_id == t.id,
                                                                 AuditLog.action == "alerts_purged"))).scalars().all()
                assert len(audit) == 1

            # wider policy: pending + dismissed, any source; case-linked and other-tenant alerts survive
            async with client() as cl:
                r = await cl.put(f"/api/v1/alert-retention/{pol['id']}", headers=h,
                                 json={"statuses": ["dismissed", "pending"], "conditions": []})
                assert r.status_code == 200, r.text
            assert await alert_retention.run_all(tenant_ids=[t.id, other.id]) == 2
            async with AsyncSessionLocal() as db:
                left = set((await db.execute(select(Alert.title).where(Alert.id.in_(alert_ids)))).scalars())
                assert left == {"old-dismissed-on-case", "old-promoted", "new-dismissed-wazuh", "other-tenant"}

            async with client() as cl:
                r = await cl.get("/api/v1/alert-retention/", headers=h)
                assert r.json()[0]["total_deleted"] == 3 and r.json()[0]["last_deleted"] == 2
                assert (await cl.delete(f"/api/v1/alert-retention/{pol['id']}", headers=h)).status_code == 204
                assert (await cl.get("/api/v1/alert-retention/", headers=h)).json() == []
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Alert).where(Alert.id.in_(alert_ids or [-1])))
                await db.commit()
    run(body)
