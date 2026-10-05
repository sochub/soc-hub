import uuid

import pytest
from types import SimpleNamespace
from sqlalchemy import delete, select

import app.db.base  # noqa: F401
from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.audit_log import AuditLog
from app.models.case import Alert, Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.case_triage import CaseTriageResult
from app.models.tenant import Tenant
from app.workflows.context import build_context
from app.workflows.nodes import NodeContext
from app.workflows.nodes.alerts import run_alert_promote
from app.workflows.templating import render, TemplateError


@pytest.mark.asyncio
async def test_promote_maps_payload_to_custom_types_and_context_exposes_attributes():
    slug = f"wf-test-{uuid.uuid4().hex[:8]}"
    case_id = None
    async with AsyncSessionLocal() as db:
        t = Tenant(name=slug, slug=slug)
        db.add(t)
        await db.flush()
        db.add_all([
            ArtifactTypeDefinition(tenant_id=t.id, key="username", label="Username", payload_key="user_name"),
            ArtifactTypeDefinition(tenant_id=t.id, key="hostname", label="Host", payload_key="host.name"),
            ArtifactTypeDefinition(tenant_id=t.id, key="port", label="Port", payload_key="port", private=True),
            ArtifactTypeDefinition(tenant_id=t.id, key="unused", label="Unused", payload_key="nothing_here"),
            ArtifactTypeDefinition(tenant_id=t.id, key="vip_mail", label="VIP", payload_key="vip.mail", private=True),
        ])
        alert = Alert(source="t", title="a", status="pending", tenant_id=t.id,
                      payload={"user_name": "bob", "host": {"name": "web-01"}, "port": 22, "ip": "1.2.3.4",
                               "vip": {"mail": "ceo@corp.example"}})
        db.add(alert)
        await db.commit()
        tid, aid = t.id, alert.id
    try:
        async with AsyncSessionLocal() as db:
            run = SimpleNamespace(tenant_id=tid, alert_id=aid, case_id=None, depth=0)
            out = await run_alert_promote(NodeContext(db=db, run=run, step=None, node={}, ctx={}), {"mode": "new", "title": "x"})
            await db.commit()
            case_id = out["case_id"]
        async with AsyncSessionLocal() as db:
            manual = SimpleNamespace(tenant_id=tid, case_id=case_id, alert_id=None, parent_run_id=None,
                                     id=-1, trigger_payload={}, is_dry_run=True)
            ctx = await build_context(db, manual)
            timeline = (await db.execute(select(TimelineEvent.content).where(TimelineEvent.case_id == case_id))).scalars().all()
        c = ctx["case"]
        assert c["attributes"]["username"] == "bob"
        assert c["attributes"]["hostname"] == "web-01"
        assert c["attributes"]["port"] == "22"          # private types are visible to workflows
        assert c["attributes"]["ip"] == "1.2.3.4"       # built-ins too
        assert "unused" not in c["attributes"]
        assert c["attributes_all"]["username"] == ["bob"]
        assert c["attributes"]["vip_mail"] == "ceo@corp.example"
        # a value mapped to a private type must not also become a public IOC artifact
        assert "ceo@corp.example" not in c["attributes_all"].get("email", [])
        assert {"type": "username", "value": "bob"} in c["artifacts"]
        assert not any("(port)" in (x or "") for x in timeline), timeline
        assert str(render("{{ case.attributes.username }}", ctx)) == "bob"
        assert str(render("{{ case.attributes.get('nope', '') }}", ctx)) == ""
        with pytest.raises(TemplateError):
            render("{{ case.attributes.nope }}", ctx)
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AuditLog).where(AuditLog.entity_type == "alert", AuditLog.entity_id == aid))
            await db.execute(delete(Alert).where(Alert.id == aid))
            if case_id:
                art_ids = (await db.execute(select(CaseArtifact.artifact_id).where(CaseArtifact.case_id == case_id))).scalars().all()
                await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == case_id))
                await db.execute(delete(Artifact).where(Artifact.id.in_(art_ids), Artifact.tenant_id == tid))
                await db.execute(delete(CaseTriageResult).where(CaseTriageResult.case_id == case_id))
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == case_id))
                await db.execute(delete(AuditLog).where(AuditLog.entity_type == "case", AuditLog.entity_id == case_id))
                await db.execute(delete(Case).where(Case.id == case_id))
            await db.execute(delete(ArtifactTypeDefinition).where(ArtifactTypeDefinition.tenant_id == tid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_alert_ioc_sweep_is_bounded():
    """The regex sweep only reads the first MAX_SCAN_CHARS of the payload."""
    from app.services.case_service import MAX_ALERT_SCAN_CHARS, add_alert_artifacts
    calls = []

    class FakeCase:
        tenant_id, id = -1, -1

    import app.services.case_service as cs
    orig = cs.detect_indicators
    cs.detect_indicators = lambda text, limit=20: calls.append(len(text)) or []

    class FakeDB:
        async def execute(self, *a, **k):
            class R:
                def scalars(self):
                    class S:
                        def all(self):
                            return []
                    return S()
            return R()
    try:
        await add_alert_artifacts(FakeDB(), case=FakeCase(), alert=SimpleNamespace(id=1, payload={"x": "a" * 5_000_000}), user_id=None)
    finally:
        cs.detect_indicators = orig
    assert calls and calls[0] <= MAX_ALERT_SCAN_CHARS

