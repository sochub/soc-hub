"""@mention tokens in case comments: `@[Display Name](user:42)`."""
import re
from typing import List, Set, Tuple

from sqlalchemy import select

from app.models.membership import TenantMembership
from app.models.user import User

MENTION_RE = re.compile(r"@\[([^\]\n]{1,100})\]\(user:(\d{1,10})\)")
_MAX_ID = 2**31 - 1  # users.id is int4; bigger ids can't exist and would make asyncpg raise
MAX_MENTIONS = 50  # ponytail: tokens past the first 50 distinct ids stay plain text


def parse_mention_ids(text: str) -> List[int]:
    seen = {}
    for m in MENTION_RE.finditer(text or ""):
        uid = int(m.group(2))
        if uid <= _MAX_ID:
            seen.setdefault(uid, None)
            if len(seen) >= MAX_MENTIONS:
                break
    return list(seen)


def _display(user: User) -> str:
    name = (user.full_name or user.email or "").replace("[", "").replace("]", "").replace("\n", " ")
    return name[:100] or "user"


async def canonicalize_mentions(db, tenant_id: int, text: str) -> Tuple[str, Set[int]]:
    ids = parse_mention_ids(text)
    if not ids:
        return text, set()
    rows = (await db.execute(
        select(User).join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id, User.id.in_(ids), User.is_active.is_(True))
    )).scalars().all()
    members = {u.id: u for u in rows}

    def sub(m: "re.Match") -> str:
        u = members.get(int(m.group(2)))
        return f"@[{_display(u)}](user:{u.id})" if u else m.group(0)

    return MENTION_RE.sub(sub, text), set(members)
