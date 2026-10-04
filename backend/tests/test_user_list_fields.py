import asyncio
import secrets
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
from sqlalchemy import delete

from app.core import security
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.main import app
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.case import TimelineEventUser


def test_timeline_user_schema_exposes_avatar_fields():
    u = SimpleNamespace(id=1, email="a@x.test", full_name="A", avatar_key="avatars/1/abcdef.webp")
    out = TimelineEventUser.model_validate(u)
    assert out.has_avatar is True and out.avatar_version == "abcdef"
    none = TimelineEventUser.model_validate(SimpleNamespace(id=2, email="b@x.test", full_name=None, avatar_key=None))
    assert none.has_avatar is False and none.avatar_version is None


def test_users_list_and_mentionable_expose_avatar_and_mfa():
    async def go():
        await engine.dispose()
        h = secrets.token_hex(3)
        uids, tid = [], None
        try:
            async with AsyncSessionLocal() as db:
                t = Tenant(name="wf-test", slug=f"wf-test-{h}-ul")
                db.add(t)
                await db.flush()
                tid = int(t.id)
                a = User(email=f"wf-test-{h}-adm@example.com", hashed_password=get_password_hash("x" * 14), is_active=True)
                b = User(email=f"wf-test-{h}-bob@example.com", hashed_password=get_password_hash("x" * 14), is_active=True,
                         full_name=f"Bob{h}", avatar_key="avatars/9/feedbeef.webp", mfa_enabled_at=datetime.now(timezone.utc))
                db.add_all([a, b])
                await db.flush()
                uids = [int(a.id), int(b.id)]
                db.add(TenantMembership(user_id=a.id, tenant_id=tid, role="admin"))
                db.add(TenantMembership(user_id=b.id, tenant_id=tid, role="analyst"))
                await db.commit()
                hdr = {"Authorization": f"Bearer {security.issue_access_token(a, tid)}"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                rows = {r["id"]: r for r in (await c.get("/api/v1/users/", headers=hdr)).json()}
                assert rows[uids[1]]["has_avatar"] is True and rows[uids[1]]["mfa_enabled"] is True
                assert rows[uids[1]]["avatar_version"] == "feedbeef"
                assert rows[uids[0]]["has_avatar"] is False and rows[uids[0]]["mfa_enabled"] is False
                m = (await c.get("/api/v1/users/mentionable", params={"q": f"Bob{h}"}, headers=hdr)).json()
                assert m and m[0]["id"] == uids[1] and m[0]["has_avatar"] is True
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantMembership).where(TenantMembership.user_id.in_(uids)))
                await db.execute(delete(User).where(User.id.in_(uids)))
                if tid:
                    await db.execute(delete(Tenant).where(Tenant.id == tid))
                await db.commit()
            await engine.dispose()
    asyncio.run(go())
