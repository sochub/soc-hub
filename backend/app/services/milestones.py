"""Case-level incident milestones: declared columns on the case override the computed defaults."""
from typing import Optional

from sqlalchemy import select

from app.models.case import Alert
from app.models.case_task import CaseTask
from app.models.ioc import IOC
from app.utils.audit import create_audit_log
from app.services.war_room_events import queue_card
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
def introduced_inversions(effective: dict, changed: set) -> list:
    """Out-of-order neighbours among the *known* milestones (an unknown one in between doesn't hide an inversion),
    reported only when this request changed one side. Pre-existing (computed) inversions never block an edit."""
    known = [(k, calc.aware(effective.get(k))) for k in KEYS if effective.get(k) is not None]
    return [(a, b) for (a, x), (b, y) in zip(known, known[1:]) if x > y and (a in changed or b in changed)]


def inversion_message(pair) -> str:
    return f"{_NAMES[pair[0]]} must not be after {_NAMES[pair[1]]}"


class MilestoneConflict(Exception):
    """A patch would introduce an out-of-order milestone pair; str(e) is the user-facing message."""


async def set_milestones(db, case, patch: dict, user_id: Optional[int]) -> dict:
    """Apply a milestone patch (column names), audit it, queue the containment card. Does not commit."""
    old = {k: getattr(case, k) for k in patch}
    for k, v in patch.items():
        setattr(case, k, v)
    ms = await case_milestones(db, case)
    bad = introduced_inversions({k: ms[k]["at"] for k in KEYS}, {k[:-3] for k in patch})
    if bad:
        for k, v in old.items():
            setattr(case, k, v)
        raise MilestoneConflict(inversion_message(bad[0]))
    iso = lambda d: {k: (v.isoformat() if v is not None else None) for k, v in d.items()}  # noqa: E731
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="milestones_changed",
                           tenant_id=case.tenant_id, user_id=user_id, changes={"old": iso(old), "new": iso(patch)})
    new_c = patch.get("contained_at")
    if new_c is not None and new_c != old.get("contained_at"):
        queue_card(db, case.tenant_id, "containment.confirmed", case.id,
                   {"at": new_c.isoformat(), "user_id": user_id})
    return ms
