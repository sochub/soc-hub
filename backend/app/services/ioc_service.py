"""IOC mutations shared by the HTTP API (and the MCP server).

All functions only flush; the caller commits.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enrichment.indicators import normalise
from app.models.artifact import Artifact
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.enrichment_result import EnrichmentResult
from app.models.ioc import IOC
from app.utils.audit import create_audit_log

ARTIFACT_IOC_TYPE = {"ip": "ip_address", "domain": "domain", "url": "url", "file_hash": "file_hash",
                     "email": "email", "other": "other"}


async def ensure_case_in_tenant(db: AsyncSession, *, tenant_id: int, case_id: int | None) -> None:
    """404 "Case not found" unless ``case_id`` is None or a case of ``tenant_id``."""
    if case_id is None:
        return
    found = (await db.execute(select(Case.id).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalar()
    if found is None:
        raise HTTPException(status_code=404, detail="Case not found")


async def create_ioc(db: AsyncSession, *, tenant_id: int, data: dict, user_id: int) -> IOC:
    await ensure_case_in_tenant(db, tenant_id=tenant_id, case_id=data.get("case_id"))
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


_RANK = {"malicious": 3, "suspicious": 2, "harmless": 1, "unknown": 0}


async def enrichment_verdicts(db: AsyncSession, tenant_id: int, iocs) -> dict:
    """Worst ok enrichment verdict per IOC id, from one query over the page's normalised indicators."""
    pairs = {}
    for ioc in iocs:
        n = normalise(ioc.ioc_type, ioc.value)
        if n:
            pairs[ioc.id] = n
    if not pairs:
        return {}
    rows = (await db.execute(select(
        EnrichmentResult.indicator_type, EnrichmentResult.indicator_value, EnrichmentResult.verdict).where(
        EnrichmentResult.tenant_id == tenant_id, EnrichmentResult.status == "ok",
        EnrichmentResult.indicator_type.in_({t for t, _ in pairs.values()}),
        EnrichmentResult.indicator_value.in_({v for _, v in pairs.values()})))).all()
    worst = {}
    for t, v, verdict in rows:
        if verdict in _RANK and _RANK[verdict] > _RANK.get(worst.get((t, v)), -1):
            worst[(t, v)] = verdict
    return {ioc_id: worst.get(pair) for ioc_id, pair in pairs.items()}
