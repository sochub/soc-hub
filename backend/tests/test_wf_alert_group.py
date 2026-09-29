"""Two concurrent group-mode promotes with the same key must produce ONE case.
Runs against the dev Postgres; cleans up only the rows it created."""
import asyncio
import uuid

import pytest
from types import SimpleNamespace

from sqlalchemy import delete, select

import app.db.base  # noqa: F401  (registers all models so mappers resolve)
from app.db.session import AsyncSessionLocal, engine
from app.models.case import Alert, Case, TimelineEvent
from app.models.case_triage import CaseTriageResult
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant
from app.workflows.nodes.alerts import find_or_create_grouped_case, run_alert_promote
from app.workflows.nodes import NodeContext, NodeError


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


@pytest.mark.asyncio
async def test_group_promote_rejects_empty_group_key():
    nctx = SimpleNamespace(db=None, run=SimpleNamespace(), after_commit=[])
    with pytest.raises(NodeError, match="group_key rendered empty"):
        await run_alert_promote(nctx, {"mode": "group", "group_key": "  ", "title": "x"})


@pytest.mark.asyncio
async def test_concurrent_promotes_same_alert_only_one_succeeds():
    slug = f"wf-test-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=slug, slug=slug)
        db.add(tenant)
        await db.flush()
        alert = Alert(source="t", title="a", payload={}, status="pending", tenant_id=tenant.id)
        db.add(alert)
        await db.commit()
        tenant_id, alert_id = tenant.id, alert.id

    async def promote():
        async with AsyncSessionLocal() as db:
            # like build_context: the alert is already in the identity map
            preloaded = (await db.execute(select(Alert).where(Alert.id == alert_id))).scalars().first()  # keep a strong ref
            run = SimpleNamespace(tenant_id=tenant_id, alert_id=alert_id, case_id=None, depth=0)
            nctx = NodeContext(db=db, run=run, step=None, node={}, ctx={})
            try:
                out = await run_alert_promote(nctx, {"mode": "new", "title": "x"})
                await asyncio.sleep(0.2)
                await db.commit()
                return out["case_id"]
            except NodeError as e:
                await db.rollback()
                return e
            finally:
                del preloaded

    results = await asyncio.gather(promote(), promote(), return_exceptions=True)
    try:
        ok = [r for r in results if isinstance(r, int)]
        errs = [r for r in results if isinstance(r, NodeError)]
        assert len(ok) == 1 and len(errs) == 1, results
        assert "already promoted" in str(errs[0])
    finally:
        async with AsyncSessionLocal() as db:
            ids = [r for r in results if isinstance(r, int)]
            await db.execute(delete(AuditLog).where(AuditLog.entity_type == "alert", AuditLog.entity_id == alert_id))
            await db.execute(delete(Alert).where(Alert.id == alert_id))
            if ids:
                await db.execute(delete(CaseTriageResult).where(CaseTriageResult.case_id.in_(ids)))
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(ids)))
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "case", AuditLog.entity_id.in_(ids)))
                await db.execute(delete(Case).where(Case.id.in_(ids)))
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
        await engine.dispose()
