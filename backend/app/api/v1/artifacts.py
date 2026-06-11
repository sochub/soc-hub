from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.models.artifact import Artifact
from app.models.case import Case, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.user import User
from app.schemas import artifact as artifact_schema

router = APIRouter()


def _artifact_with_cases(artifact: Artifact, case_ids: List[int]) -> dict:
    """Build an ArtifactWithCases-compatible dict from an ORM artifact + case_ids."""
    return {
        "id": artifact.id,
        "tenant_id": artifact.tenant_id,
        "artifact_type": artifact.artifact_type,
        "value": artifact.value,
        "description": artifact.description,
        "isolated": artifact.isolated,
        "created_at": artifact.created_at,
        "created_by": artifact.created_by,
        "case_ids": case_ids,
        "case_count": len(case_ids),
    }


@router.post("/", response_model=artifact_schema.ArtifactWithCases)
async def create_artifact(
    *,
    db: AsyncSession = Depends(deps.get_db),
    artifact_in: artifact_schema.ArtifactCreate,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Add an artifact to a case (tenant-scoped).

    If isolated=False, reuses an existing artifact with the same value+type+tenant
    and just creates a junction record. If isolated=True, always creates a new artifact.
    """
    # Verify case exists in tenant
    result = await db.execute(
        select(Case).where(Case.id == artifact_in.case_id, Case.tenant_id == tenant_id)
    )
    case = result.scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    artifact = None

    if not artifact_in.isolated:
        # Try to find an existing shared artifact with same value + type + tenant
        result = await db.execute(
            select(Artifact).where(
                Artifact.value == artifact_in.value,
                Artifact.artifact_type == artifact_in.artifact_type,
                Artifact.tenant_id == tenant_id,
                Artifact.isolated == False,  # noqa: E712
            )
        )
        artifact = result.scalars().first()

    if artifact is None:
        # Create new artifact
        artifact = Artifact(
            artifact_type=artifact_in.artifact_type,
            value=artifact_in.value,
            description=artifact_in.description,
            isolated=artifact_in.isolated,
            tenant_id=tenant_id,
            created_by=current_user.id,
        )
        db.add(artifact)
        await db.flush()  # Get artifact.id

    # Check if junction already exists
    result = await db.execute(
        select(CaseArtifact).where(
            CaseArtifact.case_id == artifact_in.case_id,
            CaseArtifact.artifact_id == artifact.id,
        )
    )
    existing_link = result.scalars().first()

    if not existing_link:
        junction = CaseArtifact(
            case_id=artifact_in.case_id,
            artifact_id=artifact.id,
            added_by=current_user.id,
        )
        db.add(junction)

        timeline = TimelineEvent(
            case_id=artifact_in.case_id,
            user_id=current_user.id,
            event_type="artifact_added",
            content=f"Added artifact: {artifact_in.value} ({artifact_in.artifact_type})"
        )
        db.add(timeline)

    await db.commit()
    await db.refresh(artifact)

    # Fetch case_ids for response
    result = await db.execute(
        select(CaseArtifact.case_id).where(CaseArtifact.artifact_id == artifact.id)
    )
    case_ids = [row[0] for row in result.all()]

    return _artifact_with_cases(artifact, case_ids)


@router.get("/", response_model=List[artifact_schema.ArtifactWithCases])
async def read_artifacts_all(
    *,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
    skip: int = 0,
    limit: int = 100,
) -> Any:
    """Get all artifacts with case info (tenant-scoped)."""
    # Get artifacts
    result = await db.execute(
        select(Artifact)
        .where(Artifact.tenant_id == tenant_id)
        .offset(skip).limit(limit)
    )
    artifacts = result.scalars().all()

    if not artifacts:
        return []

    # Batch-fetch case_ids for all artifacts
    artifact_ids = [a.id for a in artifacts]
    result = await db.execute(
        select(CaseArtifact.artifact_id, CaseArtifact.case_id)
        .where(CaseArtifact.artifact_id.in_(artifact_ids))
    )
    case_map: dict[int, list[int]] = {}
    for artifact_id, case_id in result.all():
        case_map.setdefault(artifact_id, []).append(case_id)

    return [
        _artifact_with_cases(a, case_map.get(a.id, []))
        for a in artifacts
    ]


@router.get("/case/{case_id}", response_model=List[artifact_schema.ArtifactWithCases])
async def read_artifacts_by_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Get artifacts for a specific case (tenant-scoped)."""
    # Get artifacts linked to this case
    result = await db.execute(
        select(Artifact)
        .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(
            CaseArtifact.case_id == case_id,
            Artifact.tenant_id == tenant_id,
        )
    )
    artifacts = result.scalars().all()

    if not artifacts:
        return []

    # Batch-fetch all case_ids for these artifacts
    artifact_ids = [a.id for a in artifacts]
    result = await db.execute(
        select(CaseArtifact.artifact_id, CaseArtifact.case_id)
        .where(CaseArtifact.artifact_id.in_(artifact_ids))
    )
    case_map: dict[int, list[int]] = {}
    for artifact_id, cid in result.all():
        case_map.setdefault(artifact_id, []).append(cid)

    return [
        _artifact_with_cases(a, case_map.get(a.id, []))
        for a in artifacts
    ]


@router.put("/{artifact_id}", response_model=artifact_schema.Artifact)
async def update_artifact(
    *,
    db: AsyncSession = Depends(deps.get_db),
    artifact_id: int,
    artifact_in: artifact_schema.ArtifactUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Update an artifact (tenant-scoped)."""
    result = await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.tenant_id == tenant_id,
        )
    )
    artifact = result.scalars().first()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    update_data = artifact_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(artifact, field, value)

    await db.commit()
    await db.refresh(artifact)
    return artifact


@router.get("/search/", response_model=List[artifact_schema.ArtifactWithCases])
async def search_artifacts(
    *,
    db: AsyncSession = Depends(deps.get_db),
    value: str,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Search for artifacts by value (tenant-scoped)."""
    result = await db.execute(
        select(Artifact).where(
            Artifact.value == value,
            Artifact.tenant_id == tenant_id,
        )
    )
    artifacts = result.scalars().all()

    if not artifacts:
        return []

    artifact_ids = [a.id for a in artifacts]
    result = await db.execute(
        select(CaseArtifact.artifact_id, CaseArtifact.case_id)
        .where(CaseArtifact.artifact_id.in_(artifact_ids))
    )
    case_map: dict[int, list[int]] = {}
    for artifact_id, case_id in result.all():
        case_map.setdefault(artifact_id, []).append(case_id)

    return [
        _artifact_with_cases(a, case_map.get(a.id, []))
        for a in artifacts
    ]


@router.delete("/{artifact_id}")
async def delete_artifact(
    *,
    db: AsyncSession = Depends(deps.get_db),
    artifact_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Permanently delete an artifact and all its case links (tenant-scoped)."""
    result = await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.tenant_id == tenant_id,
        )
    )
    artifact = result.scalars().first()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Get linked cases to create timeline events
    result = await db.execute(
        select(CaseArtifact.case_id).where(CaseArtifact.artifact_id == artifact_id)
    )
    linked_case_ids = [row[0] for row in result.all()]

    for cid in linked_case_ids:
        timeline = TimelineEvent(
            case_id=cid,
            user_id=current_user.id,
            event_type="artifact_removed",
            content=f"Deleted artifact: {artifact.value} ({artifact.artifact_type})"
        )
        db.add(timeline)

    # cascade will remove junction records via relationship
    await db.delete(artifact)
    await db.commit()
    return {"detail": "Artifact deleted"}


@router.delete("/{artifact_id}/case/{case_id}")
async def remove_artifact_from_case(
    *,
    db: AsyncSession = Depends(deps.get_db),
    artifact_id: int,
    case_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    tenant_id: int = Depends(deps.get_effective_tenant_id),
) -> Any:
    """Unlink an artifact from a case.

    If the artifact is isolated and has no remaining case links, delete it entirely.
    """
    # Verify artifact exists in tenant
    result = await db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.tenant_id == tenant_id,
        )
    )
    artifact = result.scalars().first()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Find and remove the junction record
    result = await db.execute(
        select(CaseArtifact).where(
            CaseArtifact.case_id == case_id,
            CaseArtifact.artifact_id == artifact_id,
        )
    )
    junction = result.scalars().first()
    if not junction:
        raise HTTPException(status_code=404, detail="Artifact is not linked to this case")

    await db.delete(junction)

    # Create timeline event
    timeline = TimelineEvent(
        case_id=case_id,
        user_id=current_user.id,
        event_type="artifact_removed",
        content=f"Removed artifact: {artifact.value} ({artifact.artifact_type})"
    )
    db.add(timeline)

    # If isolated and orphaned, delete the artifact itself
    if artifact.isolated:
        result = await db.execute(
            select(sa_func.count()).select_from(CaseArtifact).where(
                CaseArtifact.artifact_id == artifact_id
            )
        )
        remaining = result.scalar()
        # remaining includes the one we just deleted (not yet committed),
        # but since we called db.delete() it's marked for deletion.
        # After flush, count should be accurate. Let's flush first.
        await db.flush()
        result = await db.execute(
            select(sa_func.count()).select_from(CaseArtifact).where(
                CaseArtifact.artifact_id == artifact_id
            )
        )
        remaining = result.scalar()
        if remaining == 0:
            await db.delete(artifact)

    await db.commit()
    return {"detail": "Artifact removed from case"}
