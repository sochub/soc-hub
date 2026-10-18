"""Audit log model: new columns, nullable tenant/entity, FK delete rules."""
import asyncio
import secrets

import app.db.base  # noqa: F401  (registers every mapper, as the rest of the suite does)
from sqlalchemy import delete, select, text

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.models.user import User


def _run(coro):
    async def go():
        await engine.dispose()
        try:
            await coro()
        finally:
            await engine.dispose()
    asyncio.run(go())


async def _cleanup(audit_ids, tenant_id, user_id):
    # Delete only the ids this test created. Audit rows first, then tenant, then user.
    async with AsyncSessionLocal() as db:
        for aid in audit_ids:
            if aid:
                await db.execute(delete(AuditLog).where(AuditLog.id == aid))
        if tenant_id:
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        if user_id:
            await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def test_platform_row_and_fk_rules():
    async def body():
        h = secrets.token_hex(3)
        tid = uid = pid = sid = None
        try:
            async with AsyncSessionLocal() as db:
                t = Tenant(name="wf-test", slug=f"wf-test-{h}-am")
                u = User(email=f"wf-test-{h}-am@example.com", hashed_password=get_password_hash("x" * 16), is_active=True)
                db.add_all([t, u])
                await db.flush()
                tid, uid = t.id, u.id
                platform = AuditLog(category="security", action="login.failed", outcome="denied",
                                    changes={"email_domain": "example.com"})
                scoped = AuditLog(category="write", action="POST /api/v1/cases/", tenant_id=t.id, user_id=u.id,
                                  user_email=u.email, method="POST", route="/api/v1/cases/", status_code=201,
                                  request_id="r" * 36, duration_ms=3, ip="127.0.0.1", user_agent="ua", outcome="success")
                db.add_all([platform, scoped])
                await db.commit()
                pid, sid = platform.id, scoped.id
            async with AsyncSessionLocal() as db:
                await db.execute(delete(User).where(User.id == uid))
                await db.commit()
                row = (await db.execute(select(AuditLog).where(AuditLog.id == sid))).scalars().one()
                assert row.user_id is None and row.user_email.endswith("-am@example.com")  # SET NULL keeps the snapshot
                await db.execute(delete(Tenant).where(Tenant.id == tid))
                await db.commit()
                assert (await db.execute(select(AuditLog).where(AuditLog.id == sid))).first() is None  # CASCADE
                p = (await db.execute(select(AuditLog).where(AuditLog.id == pid))).scalars().one()
                assert p.tenant_id is None and p.category == "security"
        finally:
            await _cleanup([pid, sid], tid, uid)
    _run(body)


def test_server_default_category_is_change():
    async def body():
        h = secrets.token_hex(3)
        tid = rid = None
        try:
            async with AsyncSessionLocal() as db:
                t = Tenant(name="wf-test", slug=f"wf-test-{h}-ad")
                db.add(t)
                await db.flush()
                tid = t.id
                # Raw SQL omits category so the migration's server_default is what gets exercised.
                res = await db.execute(
                    text("INSERT INTO audit_logs (action, tenant_id) VALUES ('update', :t) RETURNING id, category"),
                    {"t": tid},
                )
                rid, category = res.one()
                await db.commit()
                assert category == "change"
        finally:
            await _cleanup([rid], tid, None)
    _run(body)
