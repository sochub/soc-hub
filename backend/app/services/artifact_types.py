"""Custom (tenant-defined) artifact types: resolution, private filtering, alert payload mapping."""
from typing import Any, List, Optional, Tuple

from sqlalchemy import or_, select

from app.models.artifact import Artifact, ArtifactType
from app.models.artifact_type_definition import ArtifactTypeDefinition

BUILTIN_KEYS = frozenset(t.value for t in ArtifactType)
MAX_VALUES_PER_TYPE = 20


class UnknownArtifactType(ValueError):
    pass


async def resolve_type(db, tenant_id: int, key: str) -> Tuple[ArtifactType, Optional[ArtifactTypeDefinition]]:
    """Map a type key (built-in value or tenant custom key) to (stored enum, definition-or-None)."""
    key = str(key or "").strip()
    if key in BUILTIN_KEYS:
        return ArtifactType(key), None
    d = (await db.execute(select(ArtifactTypeDefinition).where(
        ArtifactTypeDefinition.tenant_id == tenant_id, ArtifactTypeDefinition.key == key))).scalars().first()
    if d is None:
        raise UnknownArtifactType(key)
    return ArtifactType.OTHER, d


def not_private():
    """WHERE clause: artifact is built-in or of a non-private custom type."""
    private_ids = select(ArtifactTypeDefinition.id).where(ArtifactTypeDefinition.private.is_(True))
    return or_(Artifact.custom_type_id.is_(None), Artifact.custom_type_id.notin_(private_ids))


def payload_values(payload: Any, path: str) -> List[str]:
    """Scalar values at a dotted path in an alert payload; lists fan out. Deduped, capped."""
    cur = payload
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return []
        cur = cur[part]
    items = cur if isinstance(cur, list) else [cur]
    out = [str(v).strip() for v in items if isinstance(v, (str, int, float, bool)) and str(v).strip()]
    return list(dict.fromkeys(out))[:MAX_VALUES_PER_TYPE]
