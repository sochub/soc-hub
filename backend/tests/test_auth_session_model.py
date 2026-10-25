import secrets

from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.models.auth_session import AuthSession
from app.models.oauth_client import OAuthClient
from tests.guest_fixtures import make_tenant, make_user, run


def test_session_roundtrip():
    sid = secrets.token_hex(16)
    holder = {}

    async def body(ids):
        cid = holder["cid"] = f"cl-{ids.h}"
        try:
            async with AsyncSessionLocal() as db:
                t = await make_tenant(db, ids)
                assert t.mcp_enabled is False
                u = await make_user(db, ids)
                db.add(OAuthClient(id=cid, client_name="test", redirect_uris=["http://127.0.0.1/cb"]))
                await db.flush()
                db.add(AuthSession(id=sid, user_id=u.id, kind="oauth", tenant_id=t.id, client_id=cid,
                                   resource="https://x/mcp", scope="mcp", amr="pwd", tv=0,
                                   refresh_hash="a" * 64))
                await db.commit()
            async with AsyncSessionLocal() as db:
                s = (await db.execute(select(AuthSession).where(AuthSession.id == sid))).scalar_one()
                assert s.kind == "oauth" and s.client_id == cid and s.scope == "mcp"
                assert s.revoked_at is None and s.prev_refresh_hash is None
                assert s.created_at is not None and s.last_used_at is not None
                c = (await db.execute(select(OAuthClient).where(OAuthClient.id == cid))).scalar_one()
                assert c.redirect_uris == ["http://127.0.0.1/cb"] and c.created_at is not None
                uid = s.user_id
            await cleanup_user_only(uid)
            async with AsyncSessionLocal() as db:
                assert (await db.execute(select(AuthSession).where(AuthSession.id == sid))).first() is None
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(AuthSession).where(AuthSession.id == sid))
                await db.execute(delete(OAuthClient).where(OAuthClient.id == cid))
                await db.commit()

    run(body)


async def cleanup_user_only(uid):
    from app.models.user import User
    async with AsyncSessionLocal() as db:
        await db.execute(delete(User).where(User.id == uid))
        await db.commit()
