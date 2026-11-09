"""Optimistic version check for markdown saves (report, notes, investigation events)."""
from datetime import datetime, timezone
from typing import Optional



class VersionConflict(Exception):
    def __init__(self, updated_at: Optional[datetime]):
        self.updated_at = updated_at


def _sec(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def check_version(body, current: Optional[datetime]) -> None:
    """409 ``version_conflict`` when the client sent ``base_updated_at`` and it is not the row's current version.

    Omitted field: no check. Sent: both NULL, or equal to the second, passes; anything else conflicts
    (including a null base against a row that has since been saved, and a base against a never-saved row)."""
    if "base_updated_at" not in body.model_fields_set:
        return
    if _sec(body.base_updated_at) != _sec(current):
        raise VersionConflict(current)


async def version_conflict_handler(_request, exc: VersionConflict):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={"detail": "version_conflict",
                                                  "updated_at": exc.updated_at.isoformat() if exc.updated_at else None})
