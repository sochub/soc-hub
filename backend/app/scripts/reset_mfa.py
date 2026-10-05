#!/usr/bin/env python3
"""Turn off two-factor authentication for one user (break-glass recovery).

Usage:

    python -m app.scripts.reset_mfa --email someone@example.com

Clears the stored TOTP secret, marks MFA as off and bumps the user's token
version (signing them out everywhere). If one of their tenants requires
two-factor, they are asked to set it up again at their next sign-in.

Use it when nobody who could reset the account in the app is able to sign in —
for example the only super admin lost their authenticator, or SECRET_KEY was
rotated (which makes every stored MFA secret unreadable).
"""
import argparse
import asyncio
import sys

from sqlalchemy import func
from sqlalchemy.future import select

import app.db.base  # noqa: F401 — register all models before any query
from app.core.security import bump_token_version
from app.db.session import AsyncSessionLocal, engine
from app.models.user import User
from app.utils.emails import normalize_email


async def reset_mfa(email: str) -> int | None:
    """Reset MFA for the user with this email. Returns their id, or None if not found."""
    email = normalize_email(email)
    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(User).where(func.lower(User.email) == email).with_for_update()
        )).scalars().first()
        if user is None:
            return None
        user.mfa_secret_enc = None
        user.mfa_enabled_at = None
        bump_token_version(user)
        await db.commit()
        return int(user.id)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Turn off two-factor authentication for a user")
    parser.add_argument("--email", required=True, help="The user's email address")
    args = parser.parse_args(argv)

    async def run():
        try:
            return await reset_mfa(args.email)
        finally:
            await engine.dispose()

    uid = asyncio.run(run())
    if uid is None:
        print("No user with that email.", file=sys.stderr)
        return 1
    print(f"MFA reset for user id {uid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
