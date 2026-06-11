#!/usr/bin/env python3
"""CLI script to create the first SUPER_ADMIN user.

Usage (recommended — prompts for the password interactively so it never lands
in your shell history or the process list):

    python -m app.scripts.create_super_admin --email admin@example.com --name "Super Admin"

You may still pass --password for non-interactive/automated use, but be aware
it will be visible in `ps` output and shell history.
"""
import argparse
import asyncio
import getpass
import sys

from sqlalchemy.future import select

import app.db.base  # noqa: F401 — register all models before any query
from app.core.passwords import validate_password_strength
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal
from app.models.user import User


def _prompt_for_password() -> str:
    """Interactively prompt for a password (twice) and validate its strength."""
    while True:
        password = getpass.getpass("Password: ")
        try:
            validate_password_strength(password)
        except ValueError as exc:
            print(f"  ✗ {exc}", file=sys.stderr)
            continue
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("  ✗ Passwords do not match. Try again.", file=sys.stderr)
            continue
        return password


async def create_super_admin(email: str, password: str, full_name: str) -> None:
    async with AsyncSessionLocal() as db:
        # Check if user exists
        result = await db.execute(select(User).where(User.email == email))
        existing = result.scalars().first()
        if existing:
            print(f"User with email {email} already exists.")
            if not existing.is_super_admin:
                existing.is_super_admin = True
                await db.commit()
                print("Updated existing user to SUPER_ADMIN.")
            else:
                print("User is already a SUPER_ADMIN.")
            return

        user = User(
            email=email,
            hashed_password=get_password_hash(password),
            full_name=full_name,
            is_active=True,
            is_super_admin=True,  # SUPER_ADMIN is a global flag, no tenant
        )
        db.add(user)
        await db.commit()
        print(f"Super admin created: {email}")


def main():
    parser = argparse.ArgumentParser(description="Create a super admin user")
    parser.add_argument("--email", required=True, help="Email address")
    parser.add_argument(
        "--password",
        default=None,
        help="Password (omit to be prompted securely — recommended)",
    )
    parser.add_argument("--name", default="Super Admin", help="Full name")
    args = parser.parse_args()

    if args.password is None:
        password = _prompt_for_password()
    else:
        # Validate even when supplied non-interactively so we never create a
        # super-admin that couldn't be created through the normal API.
        try:
            validate_password_strength(args.password)
        except ValueError as exc:
            parser.error(str(exc))
        password = args.password

    asyncio.run(create_super_admin(args.email, password, args.name))


if __name__ == "__main__":
    main()
