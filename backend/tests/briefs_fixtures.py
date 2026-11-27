"""Shared setup for the Security briefs tests. Cleanup removes only rows created here (by id)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal
from app.models.brief import BriefItem, BriefSource
from app.models.membership import TenantMembership
from app.services import briefs
from tests.guest_fixtures import make_tenant, make_user
from tests.session_fixtures import make_web_token

UTC = timezone.utc


async def make_org(db, ids, *, enabled=True, roles=("admin", "analyst", "viewer")):
    """A tenant (briefs on by default) with one member per role. Returns (tenant, {role: auth headers}, {role: user})."""
    t = await make_tenant(db, ids)
    t.briefs_enabled = enabled
    headers, users = {}, {}
    for role in roles:
        u = await make_user(db, ids)
        u.email = f"briefs-{ids.h}-{len(ids.users)}@example.com"  # /users/me validates the address (.test is refused)
        db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role=role))
        await db.commit()
        headers[role] = {"Authorization": f"Bearer {await make_web_token(db, u, t.id)}"}
        users[role] = u
    await db.commit()
    return t, headers, users


async def make_source(db, ids, tenant=None, *, name="feed", kind="news", fmt="rss", url=None, **kw) -> BriefSource:
    """tenant=None makes a built-in-like shared source; its id is remembered for cleanup."""
    n = len(ids.__dict__.setdefault("brief_sources", []))
    s = BriefSource(tenant_id=tenant.id if tenant else None, name=name, kind=kind, format=fmt,
                    url=url or f"https://feeds-{ids.h}-{n}.example.test/rss.xml", **kw)
    db.add(s)
    await db.commit()
    ids.brief_sources.append(s.id)
    return s


async def make_item(db, source, guid, *, title=None, summary="", age=timedelta(hours=1), cves=(), now=None,
                    indicators=()) -> BriefItem:
    """indicators=None stores SQL NULL: a row from before the column existed (not computed yet)."""
    i = BriefItem(source_id=source.id, guid=guid, title=title or f"title {guid}", summary=summary, kind=source.kind,
                  url=f"https://articles.example.test/{guid}", cves=list(cves), indicators=None if indicators is None else list(indicators),
                  published_at=(now or datetime.now(UTC)) - age)
    db.add(i)
    await db.commit()
    return i


async def cleanup_sources(ids) -> None:
    """Delete the shared (tenant-less) sources this test made, plus the real built-ins if ensure_builtins ran.
    Organization sources go with their tenant."""
    async with AsyncSessionLocal() as db:
        builtin = (await db.execute(select(BriefSource.id).where(
            BriefSource.tenant_id.is_(None), BriefSource.name.in_([b["name"] for b in briefs.builtin_sources()])
        ))).scalars().all()
        await db.execute(delete(BriefSource).where(BriefSource.id.in_([*getattr(ids, "brief_sources", []), *builtin, -1])))
        await db.commit()
