"""Shape of the `changes` dict produced by apply_case_update (case.updated trigger payload)."""
import asyncio
import json

import app.db.base  # noqa: F401  (registers all mappers)
from app.models.case import Case, CaseSeverity, CaseStatus, TimelineEvent
from app.services.case_service import apply_case_update
from app.workflows.events import trigger_matches


class _DB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


def _update(case, data):
    db = _DB()
    changes = asyncio.run(apply_case_update(db, case=case, update_data=data, user_id=None))
    return changes, db.added


def _case(**kw):
    return Case(**{"id": 1, "tenant_id": 1, "title": "t", "status": CaseStatus.NEW, "severity": CaseSeverity.LOW,
                   "tags": [], **kw})


def test_enum_changes_use_values():
    changes, added = _update(_case(), {"severity": CaseSeverity.CRITICAL, "status": CaseStatus.IN_PROGRESS})
    assert changes["severity"] == {"from": "low", "to": "critical"}
    assert changes["status"] == {"from": "new", "to": "in_progress"}
    assert trigger_matches("trigger.changes.severity.to == 'critical'", {"trigger": {"changes": changes}})
    notes = [e.content for e in added if isinstance(e, TimelineEvent)]
    assert "Status changed from new to in_progress" in notes
    assert "Severity changed from low to critical" in notes
    json.dumps(changes)  # audit log + Celery payload


def test_tag_changes_have_added_and_removed():
    changes, _ = _update(_case(tags=["a", "b"]), {"tags": ["b", "x"]})
    assert changes["tags"] == {"from": ["a", "b"], "to": ["b", "x"], "added": ["x"], "removed": ["a"]}
    assert trigger_matches("'x' in trigger.changes.tags.added", {"trigger": {"changes": changes}})
    json.dumps(changes)


def test_owner_change_also_reported_as_assignee():
    changes, _ = _update(_case(owner_id=None), {"owner_id": 7})
    assert changes["owner_id"] == {"from": None, "to": 7}
    assert changes["assignee"] == {"from": None, "to": 7}
    json.dumps(changes)
