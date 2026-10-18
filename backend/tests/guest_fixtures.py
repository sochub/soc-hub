"""Shared helpers for guest tests. Cleanup deletes ONLY rows whose ids were collected here."""
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, or_

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.artifact import Artifact
from app.models.audit_log import AuditLog
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_attachment import CaseAttachment
from app.models.case_guest import CaseGuest, GuestAccount, GuestNotification
from app.models.notification import CaseFollower, Notification
from app.models.tenant import Tenant
from app.models.tenant_sso_config import TenantSSOConfig
from app.models.user import User
from sqlalchemy import select

PW = "Guest-Passw0rd!x"
DOMAIN = "dept.example"


class Ids:
    def __init__(self):
        self.h = secrets.token_hex(3)
        self.tenants, self.cases, self.users, self.emails = [], [], [], []

    def email(self, name: str, domain: str = DOMAIN) -> str:
        e = f"g-{self.h}-{name}@{domain}"
        self.emails.append(e)
        return e


async def make_tenant(db, ids: Ids, *, allow=(DOMAIN,), require_sso=False, sso=False) -> Tenant:
    t = Tenant(name="wf-test", slug=f"wf-test-{ids.h}-g{len(ids.tenants)}",
               guest_domain_allowlist=list(allow), guest_require_sso=require_sso)
    db.add(t)
    await db.flush()
    ids.tenants.append(int(t.id))
    if sso:
        db.add(TenantSSOConfig(tenant_id=t.id, enabled=True, idp_entity_id="https://idp.example/e",
                               idp_sso_url="https://idp.example/sso", idp_x509_cert="MIIB"))
    await db.commit()
    return t


async def make_user(db, ids: Ids) -> User:
    u = User(email=f"g-{ids.h}-analyst{len(ids.users)}@example.test",
             hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True)
    db.add(u)
    await db.commit()
    ids.users.append(int(u.id))
    return u


async def make_case(db, ids: Ids, tenant: Tenant, title="guest-case", **kw) -> Case:
    c = Case(title=title, tenant_id=tenant.id, description="INTERNAL ONLY", **kw)
    db.add(c)
    await db.commit()
    ids.cases.append(int(c.id))
    return c


async def make_grant(db, case: Case, email: str, permission="viewer", *, expires_in=timedelta(days=30),
                     revoked=False) -> CaseGuest:
    g = CaseGuest(tenant_id=case.tenant_id, case_id=case.id, email=email, permission=permission,
                  expires_at=datetime.now(timezone.utc) + expires_in,
                  revoked_at=datetime.now(timezone.utc) if revoked else None)
    db.add(g)
    await db.commit()
    return g


async def make_account(db, email: str, *, password=PW) -> GuestAccount:
    a = GuestAccount(email=email, password_hash=get_password_hash(password))
    db.add(a)
    await db.commit()
    return a


def session_headers(email: str, amr="link", sso_tenant_id=None, gav=None, link_tenant_id=None) -> dict:
    from app.guests import tokens
    tok = tokens.issue_session(email, amr, sso_tenant_id=sso_tenant_id, gav=gav, link_tenant_id=link_tenant_id)
    return {"Authorization": f"Bearer {tok}"}


class FakeAsyncRedis:
    """Enough of redis.asyncio for SET NX/EX + INCR counters."""
    def __init__(self):
        self.data = {}

    async def set(self, key, val, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = int(val)
        return True

    async def incr(self, key):
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    async def expire(self, key, seconds, nx=False):
        return True


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def cleanup(ids: Ids) -> None:
    async with AsyncSessionLocal() as db:
        cids, tids, uids = ids.cases or [-1], ids.tenants or [-1], ids.users or [-1]
        art_ids = (await db.execute(select(CaseArtifact.artifact_id).where(
            CaseArtifact.case_id.in_(cids)))).scalars().all()
        await db.execute(delete(GuestNotification).where(GuestNotification.case_id.in_(cids)))
        await db.execute(delete(CaseAttachment).where(CaseAttachment.case_id.in_(cids)))
        await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(cids)))
        await db.execute(delete(CaseGuest).where(CaseGuest.case_id.in_(cids)))
        await db.execute(delete(GuestAccount).where(GuestAccount.email.in_(ids.emails or ["-"])))
        await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(cids)))
        if art_ids:
            await db.execute(delete(Artifact).where(Artifact.id.in_(art_ids)))
        await db.execute(delete(Notification).where(Notification.case_id.in_(cids)))
        await db.execute(delete(CaseFollower).where(CaseFollower.case_id.in_(cids)))
        await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
        await db.execute(delete(Case).where(Case.id.in_(cids)))
        await db.execute(delete(TenantSSOConfig).where(TenantSSOConfig.tenant_id.in_(tids)))
        await db.execute(delete(User).where(User.id.in_(uids)))
        await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
        await db.commit()


def run(body) -> None:
    async def main():
        await engine.dispose()
        ids = Ids()
        try:
            await body(ids)
        finally:
            app.dependency_overrides.clear()
            await cleanup(ids)
            await engine.dispose()
    asyncio.run(main())
