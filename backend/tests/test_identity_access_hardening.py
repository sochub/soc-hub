"""Identity / access hardening (security audit 2026-11-10): case owner checks (N2), member removal cleanup (N12),
role-change guards (old #4), login input caps and logs (old #5, #6), copilot close guard (old L5) and the default
tenant at login (N1). World: tenant A (admin, analyst, case), tenant B (admin_b), root = super admin."""
import logging

from sqlalchemy import select, update

from app.api import deps
from app.auth import login_tenant
from app.core import login_throttle
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, CaseStatus
from app.models.case_task import CaseTask
from app.models.membership import TenantMembership
from app.models.notification import CaseFollower
from app.models.user import User
from tests.audit_fixtures import PW, hdr, run


async def _case(w) -> Case:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Case).where(Case.id == w.case))).scalars().one()


async def _set_owner(w, uid):
    async with AsyncSessionLocal() as db:
        await db.execute(update(Case).where(Case.id == w.case).values(owner_id=uid))
        await db.commit()


def test_create_case_rejects_foreign_owner_and_direct_close():
    """N2: the owner must be an assignable member of the tenant, and a case can't be born closed."""
    async def body(c, w):
        h = hdr(w, "analyst")
        for owner in (w.users["admin_b"], w.users["root"], 999999999):
            r = await c.post("/api/v1/cases/", headers=h, json={"title": "n2", "owner_id": owner})
            assert r.status_code == 422, (owner, r.text)
        r = await c.post("/api/v1/cases/", headers=h, json={"title": "n2", "status": "closed"})
        assert r.status_code == 422 and "outcome" in r.text, r.text
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(Case.id).where(Case.tenant_id == w.ta, Case.title == "n2"))).first() is None
    run(body)


def test_case_owner_name_is_tenant_scoped():
    """N2: a case never names an account that isn't a member of the tenant."""
    async def body(c, w):
        await _set_owner(w, w.users["admin_b"])
        r = await c.get(f"/api/v1/cases/{w.case}", headers=hdr(w, "analyst"))
        assert r.status_code == 200 and r.json()["owner_name"] is None, r.text
        assert w.emails["admin_b"] not in r.text
        await _set_owner(w, w.users["admin"])
        r = await c.get(f"/api/v1/cases/{w.case}", headers=hdr(w, "analyst"))
        assert r.json()["owner_name"] == w.emails["admin"]
    run(body)


def test_remove_member_drops_followers_and_open_tasks():
    """N12: a removed member stops following the tenant's cases and loses open task assignments."""
    async def body(c, w):
        a = w.users["analyst"]
        async with AsyncSessionLocal() as db:
            db.add(CaseFollower(case_id=w.case, user_id=a, tenant_id=w.ta))
            todo = CaseTask(case_id=w.case, tenant_id=w.ta, title="t1", status="todo", assignee_id=a)
            done = CaseTask(case_id=w.case, tenant_id=w.ta, title="t2", status="done", assignee_id=a)
            db.add_all([todo, done])
            await db.commit()
            tids = (todo.id, done.id)
        r = await c.delete(f"/api/v1/users/{a}/membership", headers=hdr(w, "admin"))
        assert r.status_code == 204, r.text
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(CaseFollower).where(CaseFollower.user_id == a))).first() is None
            assert (await db.get(CaseTask, tids[0])).assignee_id is None
            assert (await db.get(CaseTask, tids[1])).assignee_id == a  # history of finished work is kept
    run(body)


def test_role_change_guards_and_audit():
    """Old #4: no self-demotion, never demote the last admin, and every change is audited with from/to."""
    async def body(c, w):
        adm, an = w.users["admin"], w.users["analyst"]
        r = await c.put(f"/api/v1/users/{adm}/role", headers=hdr(w, "admin"), json={"role": "viewer"})
        assert r.status_code == 400 and "yourself" in r.text, r.text
        r = await c.put(f"/api/v1/users/{adm}/role", headers=hdr(w, "root"), json={"role": "analyst"})
        assert r.status_code == 400 and "last admin" in r.text, r.text
        r = await c.put(f"/api/v1/users/{an}/role", headers=hdr(w, "admin"), json={"role": "admin"})
        assert r.status_code == 200, r.text
        r = await c.put(f"/api/v1/users/{an}/role", headers=hdr(w, "admin"), json={"role": "viewer"})
        assert r.status_code == 200, r.text  # another admin remains
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(AuditLog).where(
                AuditLog.tenant_id == w.ta, AuditLog.action == "role_changed", AuditLog.entity_id == an)
                .order_by(AuditLog.id))).scalars().all()
        assert [r.changes["role"] for r in rows] == [{"from": "analyst", "to": "admin"},
                                                     {"from": "admin", "to": "viewer"}]
        assert all(r.user_id == adm for r in rows)
    run(body)


class _CountingThrottle:
    def __init__(self):
        self.reserved = 0

    async def reserve(self, key, ip):
        self.reserved += 1
        return None

    async def reset(self, key, ip):
        return None


def test_overlong_password_refused_before_throttle_and_hashing():
    """Old #5: a > 1024-char password is a plain 401, without a throttle slot or an Argon2 run."""
    async def body(c, w):
        throttle = _CountingThrottle()
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: throttle
        r = await c.post("/api/v1/auth/login/access-token",
                         data={"username": w.emails["analyst"], "password": "x" * 1025})
        assert r.status_code == 401 and r.json()["detail"] == "Incorrect email or password"
        assert throttle.reserved == 0
        r = await c.post("/api/v1/auth/login/access-token", data={"username": w.emails["analyst"], "password": PW})
        assert r.status_code == 200 and throttle.reserved == 1, r.text
    run(body)


def test_failed_login_log_has_no_email(caplog):
    """Old #6: failed logins log the domain and a short hash, one reason for every miss, never the address."""
    async def body(c, w):
        with caplog.at_level(logging.WARNING, logger="app.api.v1.auth"):
            await c.post("/api/v1/auth/login/access-token", data={"username": w.emails["analyst"], "password": "no"})
            await c.post("/api/v1/auth/login/access-token",
                         data={"username": f"nobody-{w.h}@example.com", "password": "no"})
        lines = [r.getMessage() for r in caplog.records if "failed login" in r.getMessage()]
        assert len(lines) == 2, lines
        assert all("example.com#" in m and "reason=denied" in m for m in lines)
        assert not any(w.emails["analyst"] in m or f"nobody-{w.h}" in m for m in lines)
    run(body)


def test_copilot_update_case_cannot_close_without_outcome():
    """Old L5: the copilot update_case action runs the same guards as REST (closing needs an outcome)."""
    async def body(c, w):
        r = await c.post("/api/v1/copilot/actions/execute", headers=hdr(w, "analyst"),
                         json={"type": "update_case", "case_id": w.case, "params": {"status": "closed"}})
        assert r.status_code == 422 and "outcome" in r.text, r.text
        assert (await _case(w)).status != CaseStatus.CLOSED
        r = await c.post("/api/v1/copilot/actions/execute", headers=hdr(w, "analyst"),
                         json={"type": "update_case", "case_id": w.case, "params": {"severity": "high"}})
        assert r.status_code == 200, r.text
    run(body)


def test_default_tenant_is_the_oldest_membership():
    """N1: a later membership in a lower-id tenant doesn't take over the default tenant at login."""
    async def body(c, w):
        async with AsyncSessionLocal() as db:
            db.add(TenantMembership(user_id=w.users["admin_b"], tenant_id=w.ta, role="viewer"))  # A.id < B.id
            await db.commit()
        async with AsyncSessionLocal() as db:
            u = (await db.execute(select(User).where(User.id == w.users["admin_b"]))).scalars().one()
            await db.refresh(u, ["memberships"])
            assert await deps.default_active_tenant_id(db, u) == w.tb
            assert (await login_tenant.password_login_tenant(db, u, mfa_enabled=False))[0] == w.tb
    run(body)
