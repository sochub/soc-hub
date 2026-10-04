import asyncio
import secrets
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.user import User
from app.scripts import reset_mfa
from app.utils.crypto import encrypt


def test_reset_mfa_script(capsys):
    async def setup():
        await engine.dispose()
        async with AsyncSessionLocal() as db:
            u = User(email=f"wf-test-{secrets.token_hex(3)}-rm@example.com",
                     hashed_password=get_password_hash("Old-Password-12345"), is_active=True,
                     mfa_secret_enc=encrypt("JBSWY3DPEHPK3PXP"), mfa_enabled_at=datetime.now(timezone.utc),
                     token_version=3)
            db.add(u)
            await db.commit()
            out = int(u.id), u.email
        await engine.dispose()
        return out

    async def load(uid):
        async with AsyncSessionLocal() as db:
            u = (await db.execute(select(User).where(User.id == uid))).scalars().one()
        await engine.dispose()
        return u

    async def cleanup(uid):
        async with AsyncSessionLocal() as db:
            await db.execute(delete(User).where(User.id == uid))
            await db.commit()
        await engine.dispose()

    uid, email = asyncio.run(setup())
    try:
        # main() runs its own event loop, like the CLI
        assert reset_mfa.main(["--email", email.upper()]) == 0
        out = capsys.readouterr().out
        assert out.strip() == f"MFA reset for user id {uid}" and email not in out
        u = asyncio.run(load(uid))
        assert u.mfa_secret_enc is None and u.mfa_enabled_at is None and u.token_version == 4
        assert reset_mfa.main(["--email", "wf-test-nobody-xyz@example.com"]) == 1
    finally:
        asyncio.run(cleanup(uid))
