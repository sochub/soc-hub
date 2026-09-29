"""Two concurrent group-mode promotes with the same key must produce ONE case.
Runs against the dev Postgres; cleans up only the rows it created."""
import asyncio
import uuid

import pytest
from sqlalchemy import delete

import app.db.base  # noqa: F401  (registers all models so mappers resolve)
from app.db.session import AsyncSessionLocal, engine
from app.models.case import Case, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.workflows.nodes.alerts import find_or_create_grouped_case


@pytest.mark.asyncio
async def test_concurrent_group_promotes_create_one_case():
    slug = f"wf-test-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=slug, slug=slug)
        db.add(tenant)
        await db.commit()
        tenant_id = tenant.id

    async def promote():
        async with AsyncSessionLocal() as db:
            case, created, _ = await find_or_create_grouped_case(
                db, tenant_id=tenant_id, group_key="host:web-1", window_hours=6,
                case_data={"title": "EDR on web-1", "severity": "high", "tags": ["edr"]})
            await asyncio.sleep(0.2)  # widen the race window while holding the lock
            await db.commit()
            return case.id, created

    try:
        results = await asyncio.gather(promote(), promote(), promote())
        case_ids = {cid for cid, _ in results}
        assert len(case_ids) == 1
        assert sum(1 for _, created in results if created) == 1
    finally:
        async with AsyncSessionLocal() as db:
            ids = list({cid for cid, _ in results}) if "results" in locals() else []
            if ids:
                await db.execute(delete(CaseTriageResult).where(CaseTriageResult.case_id.in_(ids)))
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids)))
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "case", AuditLog.entity_id.in_(ids)))
                await db.execute(delete(Case).where(Case.id.in_(ids)))
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
        await engine.dispose()
