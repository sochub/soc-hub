"""Guardrails for automated containment, used by the containment_guard workflow node.

A guard placed before a containment call checks the tenant's policy and either records the action in the ledger
(the run goes on) or refuses it (the run stops, the case gets a note, the ledger keeps the refusal):
- protected targets (VIP accounts, domain controllers, critical ranges) are never contained automatically;
- a tenant-wide cap on actions per rolling 24 hours;
- a cap on distinct targets contained per case (re-containing a target already on the case is fine).
Concurrent guards of one tenant are serialised with a transaction-scoped advisory lock, so caps hold."""
import fnmatch
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.containment import (DEFAULT_DAILY_CAP, DEFAULT_MAX_TARGETS_PER_CASE, ContainmentAction,
                                    ContainmentPolicy)

_LOCK_NS = 0x5C0C  # advisory-lock namespace for containment guards


def defaults() -> dict:
    return {"enabled": True, "daily_cap": DEFAULT_DAILY_CAP, "max_targets_per_case": DEFAULT_MAX_TARGETS_PER_CASE,
            "protected": []}


async def policy(db: AsyncSession, tenant_id: int) -> dict:
    row = (await db.execute(select(ContainmentPolicy).where(ContainmentPolicy.tenant_id == tenant_id))).scalars().first()
    if row is None:
        return defaults()
    return {"enabled": row.enabled, "daily_cap": row.daily_cap, "max_targets_per_case": row.max_targets_per_case,
            "protected": row.protected or []}


def protected_match(kind: str, target: str, protected: list) -> Optional[dict]:
    """The first protected entry covering this target: host/user patterns are case-insensitive globs, ip
    entries are an address or CIDR, kind "any" matches every kind."""
    t = target.strip().lower()
    for p in protected:
        if p.get("kind") not in (kind, "any"):
            continue
        pat = str(p.get("pattern") or "").strip().lower()
        if not pat:
            continue
        if kind == "ip" and p.get("kind") == "ip":
            try:
                if ipaddress.ip_address(t) in ipaddress.ip_network(pat, strict=False):
                    return p
            except ValueError:
                continue
        elif fnmatch.fnmatchcase(t, pat):
            return p
    return None


async def check(db: AsyncSession, *, tenant_id: int, case_id: Optional[int], kind: str, target: str) -> Optional[str]:
    """Why this action must not run, or None. Takes the tenant's guard lock for the rest of the transaction."""
    pol = await policy(db, tenant_id)
    if not pol["enabled"]:
        return None
    await db.execute(text("SELECT pg_advisory_xact_lock(:ns, :tid)"), {"ns": _LOCK_NS, "tid": tenant_id})
    hit = protected_match(kind, target, pol["protected"])
    if hit:
        note = f" ({hit['note']})" if hit.get("note") else ""
        return f"{target} is protected by the containment policy: {hit['kind']} {hit['pattern']}{note}"
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    used = (await db.execute(select(func.count(ContainmentAction.id)).where(
        ContainmentAction.tenant_id == tenant_id, ContainmentAction.status != "blocked",
        ContainmentAction.created_at >= since))).scalar() or 0
    if used >= pol["daily_cap"]:
        return f"the daily cap of {pol['daily_cap']} automated containment actions is reached"
    if case_id is not None:
        targets = set((await db.execute(select(ContainmentAction.target).where(
            ContainmentAction.tenant_id == tenant_id, ContainmentAction.case_id == case_id,
            ContainmentAction.status == "active"))).scalars())
        if target not in targets and len(targets) >= pol["max_targets_per_case"]:
            return (f"this case already has {len(targets)} contained targets "
                    f"(the limit is {pol['max_targets_per_case']}): contain more by hand")
    return None
