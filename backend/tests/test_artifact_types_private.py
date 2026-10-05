import uuid

import pytest
from sqlalchemy import delete, select

import app.db.base  # noqa: F401
from app.api.v1.copilot import _case_indicator_values
from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact, ArtifactType
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.tenant import Tenant
from app.services.case_service import add_artifact_to_case
from app.tasks.triage import _indicator_values


@pytest.mark.asyncio
async def test_private_artifacts_hidden_from_copilot_indicators():
    slug = f"at-test-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        t = Tenant(name=slug, slug=slug)
        db.add(t)
        await db.flush()
        d = ArtifactTypeDefinition(tenant_id=t.id, key="secret_id", label="S", private=True)
        case = Case(title="c", tenant_id=t.id)
        db.add_all([d, case])
        await db.flush()
        await add_artifact_to_case(db, case=case, artifact_type=ArtifactType.OTHER, value="s3cr3t", description=None,
                                   user_id=None, custom_type=d)
        await add_artifact_to_case(db, case=case, artifact_type=ArtifactType.IP, value="1.2.3.4", description=None, user_id=None)
        await db.commit()
        tid, cid = t.id, case.id
        try:
            assert await _case_indicator_values(db, cid, tid) == {"1.2.3.4"}
            assert await _indicator_values(db, tid, cid) == ["1.2.3.4"]  # AI triage input
        finally:
            art_ids = (await db.execute(select(CaseArtifact.artifact_id).where(CaseArtifact.case_id == cid))).scalars().all()
            await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id == cid))
            await db.execute(delete(Artifact).where(Artifact.id.in_(art_ids), Artifact.tenant_id == tid))
            await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id == cid))
            await db.execute(delete(Case).where(Case.id == cid))
            await db.execute(delete(ArtifactTypeDefinition).where(ArtifactTypeDefinition.tenant_id == tid))
            await db.execute(delete(Tenant).where(Tenant.id == tid))
            await db.commit()
    await engine.dispose()
