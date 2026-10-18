"""Shared audit test world: two tenants, admin + analyst in A, admin in B, a case in A.
Cleanup deletes ONLY rows whose ids were collected here (users/tenants cascade/set-null audit rows)."""
import asyncio
import secrets

import httpx
from sqlalchemy import delete, or_, select

from app.core import login_throttle, security
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.artifact import Artifact
from app.models.case_artifact import CaseArtifact
from app.models.case_attachment import CaseAttachment
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User

PW = "Audit-Passw0rd!x"


class _FakeThrottle:
    async def reserve(self, key, ip):
        return None

    async def reset(self, key, ip):
        return None


class World:
    pass


async def _setup() -> World:
    w = World()
    w.h = secrets.token_hex(3)
    w.secret_names = []  # secrets a test creates in tenant A; deleted by (tenant, name) at cleanup
    async with AsyncSessionLocal() as db:
        ta = Tenant(name="wf-test", slug=f"wf-test-{w.h}-aa")
        tb = Tenant(name="wf-test", slug=f"wf-test-{w.h}-ab")
        db.add_all([ta, tb])
        await db.flush()
        users = {}
        for name, tenant, role, sa in [("admin", ta, "admin", False), ("analyst", ta, "analyst", False),
                                       ("admin_b", tb, "admin", False), ("root", None, None, True)]:
            u = User(email=f"wf-test-{w.h}-{name}@example.com", hashed_password=get_password_hash(PW),
                     is_active=True, is_super_admin=sa)
            db.add(u)
            await db.flush()
            if tenant is not None:
                db.add(TenantMembership(user_id=u.id, tenant_id=tenant.id, role=role))
            users[name] = u
        c = Case(title="audit-case", tenant_id=ta.id, description="internal")
        db.add(c)
        await db.commit()
        w.ta, w.tb, w.case = int(ta.id), int(tb.id), int(c.id)
        w.users = {k: int(v.id) for k, v in users.items()}
        w.emails = {k: v.email for k, v in users.items()}
        w.tokens = {
            "admin": security.issue_access_token(users["admin"], ta.id),
            "analyst": security.issue_access_token(users["analyst"], ta.id),
            "admin_b": security.issue_access_token(users["admin_b"], tb.id),
            "root": security.issue_access_token(users["root"], ta.id),
        }
    w.ua = f"audit-test-{w.h}"  # every request this run sends carries it (see client())
    w.first_id = await _max_id()
    return w


async def _max_id() -> int:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import func
        return int((await db.execute(select(func.coalesce(func.max(AuditLog.id), 0)))).scalar_one())


def hdr(w: World, who: str) -> dict:
    return {"Authorization": f"Bearer {w.tokens[who]}"}


async def rows(w: World, **where) -> list:
    """Audit rows created since setup by THIS run, filtered by column equality. A row is ours when it sits in
    one of our throwaway tenants (incl. handler `change` rows, which carry no user agent) or carries our
    run's user agent, so a concurrent dev-server request (real tenant or NULL tenant) can't mask a gap."""
    async with AsyncSessionLocal() as db:
        q = select(AuditLog).where(AuditLog.id > w.first_id,
                                   or_(AuditLog.user_agent == w.ua, AuditLog.tenant_id.in_([w.ta, w.tb])))
        for k, v in where.items():
            q = q.where(getattr(AuditLog, k) == v)
        return list((await db.execute(q.order_by(AuditLog.id))).scalars().all())


async def _cleanup(w: World) -> None:
    async with AsyncSessionLocal() as db:
        uids = list(w.users.values())
        # Anything this run's requests produced, whatever its tenant/user (e.g. unauthenticated http.denied).
        await db.execute(delete(AuditLog).where(AuditLog.id > w.first_id, AuditLog.user_agent == w.ua))
        # Platform-level rows (tenant NULL) this test produced: delete only those from our users or our run.
        await db.execute(delete(AuditLog).where(AuditLog.id > w.first_id, AuditLog.tenant_id.is_(None),
                                                AuditLog.user_id.in_(uids)))
        await db.execute(delete(AuditLog).where(AuditLog.id > w.first_id, AuditLog.tenant_id.is_(None),
                                                AuditLog.changes["email_domain"].as_string() == f"{w.h}.invalid"))
        # Rows an upload leaves behind for OUR case only (id-scoped), before the case delete.
        await db.execute(delete(CaseAttachment).where(CaseAttachment.case_id == w.case))
        aids = list((await db.execute(select(CaseArtifact.artifact_id).where(CaseArtifact.case_id == w.case))).scalars())
        await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == w.case))
        if aids:
            await db.execute(delete(Artifact).where(Artifact.id.in_(aids)))
        await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == w.case))
        if w.secret_names:
            await db.execute(delete(TenantSecret).where(TenantSecret.tenant_id == w.ta,
                                                        TenantSecret.name.in_(w.secret_names)))
        await db.execute(delete(Case).where(Case.id == w.case))
        await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(uids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(Tenant).where(Tenant.id.in_([w.ta, w.tb])))  # cascades their audit rows
        await db.commit()


def client(ua: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                             headers={"User-Agent": ua})


def run(body) -> None:
    async def go():
        await engine.dispose()
        w = await _setup()
        app.dependency_overrides[login_throttle.get_login_throttle] = lambda: _FakeThrottle()
        app.dependency_overrides[login_throttle.get_pwd_throttle] = lambda: _FakeThrottle()
        try:
            async with client(w.ua) as c:
                await body(c, w)
        finally:
            app.dependency_overrides.clear()
            await _cleanup(w)
            await engine.dispose()
    asyncio.run(go())
