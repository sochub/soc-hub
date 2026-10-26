"""Deactivated-tenant gate for background work (Celery tasks, beat sweeps, Slack, workflows).

A tenant with ``is_active = False`` must not have anything done for it: no sends, no runs, no writes. NULL counts
as active, like everywhere else. Super-admin web access is unaffected (this is only called from background paths).
"""
from sqlalchemy import select

from app.models.tenant import Tenant

TENANT_DEACTIVATED_ERROR = "tenant deactivated"


async def tenant_active(db, tenant_id) -> bool:
    """True when the tenant row exists and is not deactivated (Slack entry points: no row, no action)."""
    if tenant_id is None:
        return True
    return (await db.execute(select(Tenant.id).where(
        Tenant.id == tenant_id, Tenant.is_active.isnot(False)))).first() is not None


async def tenant_deactivated(db, tenant_id) -> bool:
    """True only for an existing tenant whose is_active is False. Background gates use this (a missing row is not
    'deactivated': FKs make that impossible in production, and queued work for it fails on its own)."""
    if tenant_id is None:
        return False
    return (await db.execute(select(Tenant.id).where(
        Tenant.id == tenant_id, Tenant.is_active.is_(False)))).first() is not None
