"""Case-level incident milestones: declared columns on the case override the computed defaults."""
from sqlalchemy import select

from app.models.case import Alert
from app.models.case_task import CaseTask
from app.models.ioc import IOC
from app.reports import milestones as calc

KEYS = calc.KEYS
_SOURCE = {"override": "declared"}


async def case_milestones(db, case) -> dict:
    cid, tid = case.id, case.tenant_id
    ioc_seen = (await db.execute(select(IOC.first_seen).where(
        IOC.case_id == cid, IOC.tenant_id == tid, IOC.first_seen.isnot(None)))).scalars().all()
    alert_created = (await db.execute(select(Alert.created_at).where(
        Alert.case_id == cid, Alert.tenant_id == tid, Alert.created_at.isnot(None)))).scalars().all()
    contained = (await db.execute(select(CaseTask.completed_at).where(
        CaseTask.case_id == cid, CaseTask.tenant_id == tid, CaseTask.status == "done",
        CaseTask.phase == "containment", CaseTask.completed_at.isnot(None)))).scalars().all()
    overrides = {k: getattr(case, f"{k}_at") for k in KEYS if getattr(case, f"{k}_at") is not None}
    out = calc.compute(ioc_first_seen=list(ioc_seen), alert_created=list(alert_created),
                       case_created=case.created_at, contained_candidates=list(contained),
                       resolved_at=case.resolved_at, overrides=overrides)
    for k in KEYS:
        out[k]["source"] = _SOURCE.get(out[k]["source"], out[k]["source"])
    return out


_NAMES = {"first_seen": "First seen", "detected": "Detected", "contained": "Contained", "recovered": "Recovered"}
_PAIRS = (("first_seen", "detected"), ("detected", "contained"), ("contained", "recovered"))


def introduced_inversions(effective: dict, changed: set) -> list:
    """Adjacent pairs out of order where at least one side was changed by this request.
    Pre-existing (computed) inversions never block an unrelated edit."""
    bad = []
    for a, b in _PAIRS:
        x, y = calc.aware(effective.get(a)), calc.aware(effective.get(b))
        if x is not None and y is not None and x > y and (a in changed or b in changed):
            bad.append((a, b))
    return bad


def inversion_message(pair) -> str:
    return f"{_NAMES[pair[0]]} must not be after {_NAMES[pair[1]]}"
