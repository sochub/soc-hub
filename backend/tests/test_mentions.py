import asyncio
import secrets

from sqlalchemy import delete

import app.db.base  # noqa: F401  (register all models)
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.membership import TenantMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.notifications.mentions import MAX_MENTIONS, parse_mention_ids, canonicalize_mentions


def test_parse_ids_unique_in_order():
    assert parse_mention_ids("hi @[A](user:3) and @[B](user:1) @[A](user:3)") == [3, 1]


def test_parse_skips_ids_beyond_int4_and_caps_count():
    assert parse_mention_ids("@[a](user:2147483648) @[b](user:9999999999) @[c](user:2147483647)") == [2147483647]
    many = " ".join(f"@[u](user:{i})" for i in range(1, 80))
    assert parse_mention_ids(many) == list(range(1, MAX_MENTIONS + 1))


def test_parse_ignores_malformed():
    assert parse_mention_ids("@[A](user:x) @A @[](user:2) @[A]\n(user:4) @[x](user:12345678901)") == []


async def _scenario():
    await engine.dispose()
    h = secrets.token_hex(3)
    ids = {"tenant_a": None, "tenant_b": None, "user_u1": None, "user_u2": None}
    try:
        async with AsyncSessionLocal() as db:
            # Create two tenants
            ta = Tenant(name="wf-test-a", slug=f"wf-test-{h}-a")
            tb = Tenant(name="wf-test-b", slug=f"wf-test-{h}-b")
            db.add_all([ta, tb])
            await db.flush()
            ids["tenant_a"], ids["tenant_b"] = ta.id, tb.id

            # Create two users
            u1 = User(email=f"u1-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True, full_name="Real Name")
            u2 = User(email=f"u2-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True, full_name="User Two")
            db.add_all([u1, u2])
            await db.flush()
            ids["user_u1"], ids["user_u2"] = u1.id, u2.id

            # Add memberships: U1 to Tenant A, U2 to Tenant B
            db.add(TenantMembership(user_id=u1.id, tenant_id=ta.id, role="analyst"))
            db.add(TenantMembership(user_id=u2.id, tenant_id=tb.id, role="analyst"))
            await db.commit()

            # Test 1: canonicalize_rewrites_display_name
            text, valid_ids = await canonicalize_mentions(db, ta.id, f"ping @[CEO](user:{u1.id})")
            assert text == f"ping @[Real Name](user:{u1.id})", f"Expected rewritten text, got {text}"
            assert valid_ids == {u1.id}, f"Expected {{u1.id}}, got {valid_ids}"

            # Test 2: canonicalize_foreign_and_unknown_untouched
            # U2 is not in tenant A, 999999999 doesn't exist
            text, valid_ids = await canonicalize_mentions(db, ta.id, f"@[Other](user:{u2.id}) @[Unknown](user:999999999)")
            assert text == f"@[Other](user:{u2.id}) @[Unknown](user:999999999)", f"Expected unchanged text, got {text}"
            assert valid_ids == set(), f"Expected empty set, got {valid_ids}"

    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id.in_([ids["tenant_a"], ids["tenant_b"]])))
            await db.execute(delete(User).where(User.id.in_([ids["user_u1"], ids["user_u2"]])))
            await db.execute(delete(Tenant).where(Tenant.id.in_([ids["tenant_a"], ids["tenant_b"]])))
            await db.commit()
    await engine.dispose()


def test_canonicalize_rewrites_display_name():
    asyncio.run(_scenario())


def test_canonicalize_foreign_and_unknown_untouched():
    asyncio.run(_scenario())


def test_display_name_without_full_name():
    """User with no full_name gets their email as the display name."""
    async def _test():
        await engine.dispose()
        h = secrets.token_hex(3)
        ids = {"tenant": None, "user": None}
        try:
            async with AsyncSessionLocal() as db:
                t = Tenant(name="wf-test-email", slug=f"wf-test-{h}-email")
                u = User(email=f"test-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)), is_active=True, full_name=None)
                db.add_all([t, u])
                await db.flush()
                ids["tenant"], ids["user"] = t.id, u.id
                db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
                await db.commit()

                text, valid_ids = await canonicalize_mentions(db, t.id, "x @[big](user:9999999999)")
                assert (text, valid_ids) == ("x @[big](user:9999999999)", set()), "out-of-range id must stay inert"

                text, valid_ids = await canonicalize_mentions(db, t.id, f"ping @[Old Name](user:{u.id})")
                assert text == f"ping @[test-{h}@example.test](user:{u.id})", f"Expected email-based rewrite, got {text}"
                assert valid_ids == {u.id}, f"Expected {{u.id}}, got {valid_ids}"

                u.is_active = False
                await db.commit()
                text, valid_ids = await canonicalize_mentions(db, t.id, f"ping @[Old Name](user:{u.id})")
                assert (text, valid_ids) == (f"ping @[Old Name](user:{u.id})", set()), "deactivated users are not mentionable"

        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(TenantMembership).where(TenantMembership.tenant_id == ids["tenant"]))
                await db.execute(delete(User).where(User.id == ids["user"]))
                await db.execute(delete(Tenant).where(Tenant.id == ids["tenant"]))
                await db.commit()
        await engine.dispose()

    asyncio.run(_test())
