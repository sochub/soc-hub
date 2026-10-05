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


@pytest.mark.asyncio
async def test_promote_extracts_artifacts_and_manual_run_sees_alert():
    """Promote copies payload IOCs onto the case; a case-only run (manual) still gets `alert` in context."""
    from app.models.artifact import Artifact
    from app.models.case_artifact import CaseArtifact
    from app.workflows.context import build_context
    slug = f"wf-test-{uuid.uuid4().hex[:8]}"
    case_id = None
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=slug, slug=slug)
        db.add(tenant)
        await db.flush()
        alert = Alert(source="t", title="a", status="pending", tenant_id=tenant.id,
                      payload={"ip": "1.2.3.4", "country": "Argentina", "user_name": "santi@sochub.io"})
        db.add(alert)
        await db.commit()
        tenant_id, alert_id = tenant.id, alert.id
    try:
        async with AsyncSessionLocal() as db:
            run = SimpleNamespace(tenant_id=tenant_id, alert_id=alert_id, case_id=None, depth=0)
            out = await run_alert_promote(NodeContext(db=db, run=run, step=None, node={}, ctx={}), {"mode": "new", "title": "x"})
            await db.commit()
            case_id = out["case_id"]
        async with AsyncSessionLocal() as db:
            manual = SimpleNamespace(tenant_id=tenant_id, case_id=case_id, alert_id=None, parent_run_id=None,
                                     id=-1, trigger_payload={"event": "manual"}, is_dry_run=True)
            ctx = await build_context(db, manual)
        assert ctx["alert"]["payload"]["country"] == "Argentina"
        assert {(a["type"], a["value"]) for a in ctx["case"]["artifacts"]} == {("ip", "1.2.3.4"), ("email", "santi@sochub.io")}
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.entity_type == "alert", AuditLog.entity_id == alert_id))
            await db.execute(delete(Alert).where(Alert.id == alert_id))
            if case_id:
                art_ids = (await db.execute(select(CaseArtifact.artifact_id).where(CaseArtifact.case_id == case_id))).scalars().all()
                await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == case_id))
                await db.execute(delete(Artifact).where(Artifact.id.in_(art_ids), Artifact.tenant_id == tenant_id))
                await db.execute(delete(CaseTriageResult).where(CaseTriageResult.case_id == case_id))
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == case_id))
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "case", AuditLog.entity_id == case_id))
                await db.execute(delete(Case).where(Case.id == case_id))
            await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
            await db.commit()
        await engine.dispose()
