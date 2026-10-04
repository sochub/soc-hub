"""@mention tokens in case comments: `@[Display Name](user:42)`."""
import re
from typing import List, Set, Tuple

from sqlalchemy import select

from app.models.membership import TenantMembership
from app.models.user import User

MENTION_RE = re.compile(r"@\[([^\]\n]{1,100})\]\(user:(\d{1,10})\)")


def parse_mention_ids(text: str) -> List[int]:
    seen: List[int] = []
    for m in MENTION_RE.finditer(text or ""):
        uid = int(m.group(2))
        if uid not in seen:
            seen.append(uid)
    return seen


def _display(user: User) -> str:
    name = (user.full_name or user.email or "").replace("[", "").replace("]", "").replace("\n", " ")
    return name[:100] or "user"


async def canonicalize_mentions(db, tenant_id: int, text: str) -> Tuple[str, Set[int]]:
    ids = parse_mention_ids(text)
    if not ids:
        return text, set()
    rows = (await db.execute(
        select(User).join(TenantMembership, TenantMembership.user_id == User.id)
        .where(TenantMembership.tenant_id == tenant_id, User.id.in_(ids))
    )).scalars().all()
    members = {u.id: u for u in rows}

    def sub(m: "re.Match") -> str:
        u = members.get(int(m.group(2)))
        return f"@[{_display(u)}](user:{u.id})" if u else m.group(0)

    return MENTION_RE.sub(sub, text), set(members)
