"""MSSP portfolio: only tenants the session could switch into (login method, MFA, active, membership), per-tenant
metrics, the merged queue order, the client digest, recipients (tenant admins only) and the weekly email job."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select

from app.api import deps
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Alert, Case, CaseSeverity, CaseStatus
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.services import email_service, portfolio
from app.tasks import portfolio as portfolio_task


async def _scenario(monkeypatch):
    await engine.dispose()
    h = secrets.token_hex(3)
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        a = Tenant(name=f"Acme {h}", slug=f"pf-{h}-a")
        b = Tenant(name=f"Bravo {h}", slug=f"pf-{h}-b")
        sso = Tenant(name="SSO only", slug=f"pf-{h}-s", member_login_method="saml")
        mfa = Tenant(name="Needs MFA", slug=f"pf-{h}-m", require_mfa=True)
        off = Tenant(name="Deactivated", slug=f"pf-{h}-o", is_active=False)
        stranger = Tenant(name="Not mine", slug=f"pf-{h}-x")
        user = User(email=f"pf-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                    is_active=True, full_name="Mia MSSP")
        tenants = [a, b, sso, mfa, off, stranger]
        db.add_all(tenants + [user])
        await db.flush()
        tids = [t.id for t in tenants]
        db.add_all([TenantMembership(user_id=user.id, tenant_id=a.id, role="admin"),
                    TenantMembership(user_id=user.id, tenant_id=b.id, role="analyst")]
                   + [TenantMembership(user_id=user.id, tenant_id=t.id, role="analyst") for t in (sso, mfa, off)])
        old_crit = Case(title="old critical", tenant_id=a.id, severity=CaseSeverity.CRITICAL, status=CaseStatus.OPEN,
                        created_at=now - timedelta(days=5))  # far past the default critical SLA
        new_low = Case(title="new low", tenant_id=b.id, severity=CaseSeverity.LOW, status=CaseStatus.NEW,
                       owner_id=user.id)
        new_high = Case(title="new high", tenant_id=b.id, severity=CaseSeverity.HIGH, status=CaseStatus.NEW)
        done = Case(title="done", tenant_id=a.id, severity=CaseSeverity.MEDIUM, status=CaseStatus.RESOLVED,
                    outcome="false_positive", created_at=now - timedelta(hours=10), resolved_at=now - timedelta(hours=4))
        hidden = Case(title="sso secret", tenant_id=sso.id, status=CaseStatus.OPEN)
        db.add_all([old_crit, new_low, new_high, done, hidden,
                    Alert(tenant_id=a.id, source="edr", external_id=f"{h}-1", title="x", status="pending"),
                    Alert(tenant_id=a.id, source="edr", external_id=f"{h}-2", title="y", status="dismissed")])
        await db.commit()
        await db.refresh(user, ["memberships"])
        uid = user.id
    extra_uid = -1
    try:
        async with AsyncSessionLocal() as db:
            ov = await portfolio.overview(db, user)
        names = [t["name"] for t in ov["tenants"]]
        assert names == [f"Acme {h}", f"Bravo {h}"], names  # not SSO-only, MFA-required, deactivated or foreign
        assert ov["hidden_tenants"] == 2
        acme = ov["tenants"][0]
        assert acme["open_cases"] == 1 and acme["sla_breached"] == 1 and acme["pending_alerts"] == 1
        assert acme["mttr_hours_30d"] == 6.0 and acme["role"] == "admin"
        bravo = ov["tenants"][1]
        assert bravo["unassigned"] == 1 and bravo["by_severity"]["high"] == 1
        titles = [q["title"] for q in ov["queue"]]
        assert titles == ["old critical", "new high", "new low"], titles  # breached first, then severity
        assert ov["queue"][2]["owner"] == "Mia MSSP" and ov["queue"][0]["tenant_name"] == f"Acme {h}"
        assert "sso secret" not in titles
        # a super admin with no memberships sees every active tenant it may enter
        async with AsyncSessionLocal() as db:
            boss = User(email=f"pf-{h}-sa@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                        is_active=True, is_super_admin=True)
            db.add(boss)
            await db.commit()
            await db.refresh(boss, ["memberships"])
            extra_uid = boss.id
            seen = {t.id for t, _ in (await portfolio.enterable_tenants(db, boss))[0]}
        assert {tids[0], tids[1], tids[5]} <= seen and tids[4] not in seen  # not the deactivated one

        app.dependency_overrides[deps.get_current_active_user] = lambda: user
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            d = (await c.get(f"/api/v1/portfolio/{tids[0]}/digest?days=7")).json()
            assert d["cases_opened"] == 2 and d["cases_resolved"] == 1 and d["outcomes"] == {"false_positive": 1}
            assert d["alerts_received"] == 2 and d["can_edit_recipients"] is True
            assert "Cases resolved: 1 (1 false positive)" in d["text"]
            for tid in (tids[2], tids[5]):  # policy-hidden and foreign tenants are 404
                assert (await c.get(f"/api/v1/portfolio/{tid}/digest")).status_code == 404
            assert (await c.put(f"/api/v1/portfolio/{tids[1]}/digest-recipients",
                                json={"recipients": ["x@example.com"]})).status_code == 403  # analyst there
            r = await c.put(f"/api/v1/portfolio/{tids[0]}/digest-recipients",
                            json={"recipients": ["CISO@Acme.com", "ciso@acme.com"]})
            assert r.json() == {"recipients": ["ciso@acme.com"]}, r.text

        sent = []
        monkeypatch.setattr(email_service, "_send_email", lambda to, subj, body, log_recipient=True: sent.append((to, subj, body)) or True)
        await engine.dispose()
        await asyncio.to_thread(portfolio_task.send_client_digests_task.run)
        mine = [s for s in sent if s[0] == "ciso@acme.com"]
        assert len(mine) == 1 and "weekly security summary" in mine[0][1] and "old critical" in mine[0][2]
        await asyncio.to_thread(portfolio_task.send_client_digests_task.run)  # not again within a week
        assert len([s for s in sent if s[0] == "ciso@acme.com"]) == 1
        async with AsyncSessionLocal() as db:
            assert "digest_recipients_changed" in set((await db.execute(select(AuditLog.action).where(
                AuditLog.tenant_id == tids[0]))).scalars())
    finally:
        app.dependency_overrides.clear()
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            await db.execute(delete(Alert).where(Alert.tenant_id.in_(tids)))
            await db.execute(delete(Case).where(Case.tenant_id.in_(tids)))
            await db.execute(delete(TenantMembership).where(TenantMembership.user_id == uid))
            await db.execute(delete(User).where(User.id.in_([uid, extra_uid])))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
        await engine.dispose()


def test_portfolio(monkeypatch):
    asyncio.run(_scenario(monkeypatch))
