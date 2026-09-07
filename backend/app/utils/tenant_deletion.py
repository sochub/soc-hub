from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.models.case import Case, Alert, TimelineEvent, CaseLink
from app.models.case_artifact import CaseArtifact
from app.models.artifact import Artifact
from app.models.audit_log import AuditLog
from app.models.invitation import Invitation
from app.models.ioc import IOC
from app.models.copilot_session import CopilotSession


async def delete_tenant_cascade(db: AsyncSession, tenant: Tenant) -> None:
    """Permanently delete a tenant and everything scoped to it.

    Many tenant_id/case_id foreign keys in this schema predate `ON DELETE
    CASCADE` being used consistently (only newer tables like case_tasks,
    case_triage_results, memberships, playbooks, webhooks, and SSO config
    have it) — a plain `db.delete(tenant)` would fail with a foreign-key
    violation the moment the tenant has any real history. This deletes the
    non-cascading tables explicitly, in dependency order; the tenant's own
    delete then cascades the remaining tables that already support it.

    Caller is responsible for checking `tenant.is_active is False` and
    committing/handling errors.
    """
    case_ids = (
        await db.execute(select(Case.id).where(Case.tenant_id == tenant.id))
    ).scalars().all()

    if case_ids:
        await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(case_ids)))
        await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(case_ids)))

    await db.execute(delete(Alert).where(Alert.tenant_id == tenant.id))
    await db.execute(delete(CaseLink).where(CaseLink.tenant_id == tenant.id))
    await db.execute(delete(IOC).where(IOC.tenant_id == tenant.id))
    await db.execute(delete(CopilotSession).where(CopilotSession.tenant_id == tenant.id))

    # case_tasks / case_triage_results cascade from case_id already.
    await db.execute(delete(Case).where(Case.tenant_id == tenant.id))

    await db.execute(delete(Artifact).where(Artifact.tenant_id == tenant.id))
    await db.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant.id))
    await db.execute(delete(Invitation).where(Invitation.tenant_id == tenant.id))

    # Memberships, playbook_templates (+ task templates), webhooks, and
    # tenant_sso_configs all already have ON DELETE CASCADE from tenant_id.
    await db.delete(tenant)
