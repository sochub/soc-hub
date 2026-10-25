"""IOC mutations shared by the HTTP API (and the MCP server).

All functions only flush; the caller commits.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.utils.audit import create_audit_log

ARTIFACT_IOC_TYPE = {"ip": "ip_address", "domain": "domain", "url": "url", "file_hash": "file_hash",
                     "email": "email", "other": "other"}


async def create_ioc(db: AsyncSession, *, tenant_id: int, data: dict, user_id: int) -> IOC:
    ioc = IOC(**data, tenant_id=tenant_id, created_by=user_id)
    db.add(ioc)
    await db.flush()
    await create_audit_log(db=db, entity_type="ioc", entity_id=ioc.id, action="create",
                           tenant_id=tenant_id, user_id=user_id)
    await db.flush()
    return ioc


async def promote_artifact(db: AsyncSession, *, artifact: Artifact, tenant_id: int, case_id: int | None,
                           user_id: int) -> tuple[IOC, bool]:
    """Promote an artifact to a tenant IOC; idempotent on (value, ioc_type). Returns (ioc, created).

    Raises 400 when ``case_id`` is given but the artifact is not attached to that tenant case."""
    if case_id is not None:
        linked = (await db.execute(
            select(CaseArtifact.id).join(Case, Case.id == CaseArtifact.case_id)
            .where(CaseArtifact.case_id == case_id, CaseArtifact.artifact_id == artifact.id,
                   Case.tenant_id == tenant_id)
        )).first()
        if not linked:
            raise HTTPException(status_code=400, detail="Artifact is not attached to that case")
    # ponytail: custom artifact types map to "other"
    ioc_type = ARTIFACT_IOC_TYPE.get(artifact.type_key, "other")
    ioc = (await db.execute(select(IOC).where(IOC.tenant_id == tenant_id, IOC.value == artifact.value,
                                              IOC.ioc_type == ioc_type))).scalars().first()
    if ioc is not None:
        return ioc, False
    ioc = IOC(tenant_id=tenant_id, case_id=case_id, ioc_type=ioc_type, value=artifact.value, source="artifact",
              tlp="amber", threat_level="medium", description=artifact.description, created_by=user_id)
    db.add(ioc)
    await db.flush()
    await create_audit_log(db=db, entity_type="ioc", entity_id=ioc.id, action="create", tenant_id=tenant_id,
                           user_id=user_id, changes={"promoted_from_artifact": artifact.id})
    await db.flush()
    return ioc, True
