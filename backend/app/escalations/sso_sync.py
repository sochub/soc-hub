"""Login-time sync from an IdP group claim (SAML attribute / OIDC claim): the member's role, and escalation groups.

A group with a non-empty `idp_groups` is *mapped*: the IdP decides who is in it. Unmapped groups are never touched.
"""
import logging
from typing import Any, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.escalation import UserGroup, UserGroupMember
from app.models.membership import TenantMembership
from app.utils.audit import create_audit_log

logger = logging.getLogger(__name__)
MAX_VALUES = 500


def _clean(values: List[Any]) -> List[str]:
    return [v.strip() for v in values if isinstance(v, str) and v.strip()][:MAX_VALUES]


def oidc_groups(claims: dict, claim: str) -> Optional[List[str]]:
    """Group values from verified ID-token claims; None = not usable (absent, or Entra group overage)."""
    try:
        names = claims.get("_claim_names")
        if isinstance(names, dict) and claim in names:
            logger.warning("OIDC group overage: %r is in _claim_names, groups not synced", claim)
            return None
        raw = claims.get(claim)
        if isinstance(raw, str):
            # names may contain spaces; commas separate only plain names, never inside a DN (has "=")
            return _clean([raw] if "=" in raw else raw.split(","))
        if isinstance(raw, list):
            return _clean(raw)
    except Exception:
        logger.warning("OIDC group claim could not be parsed, groups not synced")
    return None


def saml_groups(attrs: dict, claim: str) -> Optional[List[str]]:
    raw = attrs.get(claim)
    return _clean(raw) if isinstance(raw, list) else None


async def sync_user_groups(db: AsyncSession, *, tenant_id: int, user_id: int,
                           idp_groups: Optional[List[str]]) -> dict:
    """Make the user's membership of every mapped group match `idp_groups`. Flush only; the caller commits."""
    result: dict = {"added": [], "removed": []}
    if idp_groups is None:  # claim absent: an IdP misconfiguration must not wipe groups
        return result
    m = (await db.execute(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id))).scalars().first()
    if m is None:
        return result
    asserted = {g.casefold() for g in idp_groups}
    mapped = [g for g in (await db.execute(select(UserGroup).where(UserGroup.tenant_id == tenant_id))).scalars()
              if g.idp_groups]
    if not mapped:
        return result
    current = set((await db.execute(select(UserGroupMember.group_id).where(
        UserGroupMember.membership_id == m.id, UserGroupMember.group_id.in_([g.id for g in mapped])))).scalars())
    eligible = m.role in ("analyst", "admin")  # viewers can't be in escalation groups
    for g in sorted(mapped, key=lambda x: x.id):
        want = eligible and any(str(v).casefold() in asserted for v in g.idp_groups)
        if want and g.id not in current:
            db.add(UserGroupMember(group_id=g.id, membership_id=m.id))
            result["added"].append(g.id)
        elif not want and g.id in current:
            await db.execute(delete(UserGroupMember).where(
                UserGroupMember.group_id == g.id, UserGroupMember.membership_id == m.id))
            result["removed"].append(g.id)
    if result["added"] or result["removed"]:
        await db.flush()
        await create_audit_log(db, "user", user_id, "user_group_sso_sync", tenant_id, user_id, dict(result))
    return result


ROLE_ORDER = ("admin", "analyst", "viewer")  # highest first


async def sync_user_role(db: AsyncSession, *, tenant_id: int, user_id: int, idp_groups: Optional[List[str]],
                         role_groups: dict, default_role: str) -> Optional[dict]:
    """Set the member's role from their IdP groups when the tenant maps groups to roles: the highest role with a
    matching group, else the default (JIT) role. Never demotes the tenant's last admin. Flush only; the caller
    commits. Returns {"from", "to"} when the role changed."""
    if not any(role_groups.get(r) for r in ROLE_ORDER):
        return None  # role mapping off: roles are managed in SOC Hub
    if idp_groups is None:  # claim absent: an IdP misconfiguration must not change anyone's role
        return None
    m = (await db.execute(select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id))).scalars().first()
    if m is None:
        return None
    asserted = {g.casefold() for g in idp_groups}
    matched = next((r for r in ROLE_ORDER if any(str(g).casefold() in asserted for g in role_groups.get(r) or [])), None)
    new = matched or (default_role if default_role in ("analyst", "viewer") else "viewer")
    if new == m.role:
        return None
    if m.role == "admin":
        others = (await db.execute(select(func.count()).select_from(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.role == "admin",
            TenantMembership.user_id != user_id))).scalar_one()
        if not others:
            await create_audit_log(db, "user", user_id, "sso_role_sync_blocked", tenant_id, user_id,
                                   {"role": m.role, "idp_role": new, "reason": "last_admin"})
            return None
    change = {"from": m.role, "to": new, "matched": matched is not None}
    m.role = new
    await db.flush()
    await create_audit_log(db, "user", user_id, "sso_role_sync", tenant_id, user_id, dict(change))
    return change
