"""Closing a case needs an outcome AND a non-blank closing note on every path (REST, create, workflow node,
shared service rule). Values may come from the request or already be stored on the case."""
from types import SimpleNamespace

import pytest

from app.api import deps
from app.models.case import Case, CaseStatus
from app.services.case_service import CaseClosureIncomplete, apply_case_update, require_closure_fields
from app.workflows.nodes import NodeContext, NodeError
from app.workflows.nodes.cases import run_case_update
from tests.guest_fixtures import client, make_case, make_tenant, make_user, run


def _case(status=CaseStatus.OPEN, outcome=None, closing_note=None):
    return Case(title="x", status=status, outcome=outcome, closing_note=closing_note)


@pytest.mark.parametrize("data,case,missing", [
    ({"status": "closed", "closing_note": "why"}, _case(), "An outcome is required"),
    ({"status": "closed", "outcome": "unknown"}, _case(), "A closing note is required"),
    ({"status": "closed", "outcome": "unknown", "closing_note": "  \n "}, _case(), "A closing note is required"),
    ({"status": "closed"}, _case(), "An outcome and a closing note are required"),
    ({"status": "closed"}, _case(outcome="unknown", closing_note="   "), "A closing note is required"),
    ({"status": "closed"}, None, "An outcome and a closing note are required"),  # create
    # an already-closed case can't have either field blanked
    ({"closing_note": ""}, _case(CaseStatus.CLOSED, "unknown", "why"), "A closing note is required"),
    ({"outcome": None}, _case(CaseStatus.CLOSED, "unknown", "why"), "An outcome is required"),
])
def test_rule_refuses(data, case, missing):
    with pytest.raises(CaseClosureIncomplete) as e:
        require_closure_fields(data, case)
    assert e.value.status_code == 422 and e.value.detail.startswith(missing)


@pytest.mark.parametrize("data,case", [
    ({"status": "closed", "outcome": "false_positive", "closing_note": "benign"}, _case()),
    ({"status": CaseStatus.CLOSED}, _case(outcome="unknown", closing_note="stored")),  # stored values
    ({"status": "closed", "closing_note": "new"}, _case(outcome="legitimate")),  # mixed
    ({"status": "resolved"}, _case()),  # no Resolved step forced, and resolving needs nothing
    ({"title": "renamed"}, _case(CaseStatus.CLOSED)),  # legacy closed case: other edits stay allowed
    ({"status": "open"}, _case(CaseStatus.CLOSED)),  # reopening
])
def test_rule_allows(data, case):
    require_closure_fields(data, case)


async def _setup(ids):
    from app.db.session import AsyncSessionLocal
    from app.main import app
    db = AsyncSessionLocal()
    t = await make_tenant(db, ids)
    user = await make_user(db, ids)
    app.dependency_overrides[deps.get_current_active_user] = lambda: user
    app.dependency_overrides[deps.get_effective_tenant_id] = lambda: t.id
    app.dependency_overrides[deps.require_analyst_or_above] = lambda: user
    return db, t


def test_rest_close_requires_outcome_and_note():
    async def body(ids):
        db, t = await _setup(ids)
        try:
            case = await make_case(db, ids, t, status=CaseStatus.OPEN)
            stored = await make_case(db, ids, t, status=CaseStatus.OPEN, outcome="unknown", closing_note="stored note")
            cid = case.id
            async with client() as c:
                url = f"/api/v1/cases/{cid}"
                for body_, msg in [
                    ({"status": "closed"}, "An outcome and a closing note are required to close a case"),  # kanban drag
                    ({"status": "closed", "closing_note": "benign"}, "An outcome is required to close a case"),
                    ({"status": "closed", "outcome": "false_positive"}, "A closing note is required to close a case"),
                    ({"status": "closed", "outcome": "false_positive", "closing_note": "   "},
                     "A closing note is required to close a case"),
                ]:
                    r = await c.put(url, json=body_)
                    assert r.status_code == 422 and r.json()["detail"] == msg, (body_, r.text)
                await db.refresh(case)
                assert case.status == CaseStatus.OPEN and case.outcome is None and case.closing_note is None
                r = await c.put(url, json={"status": "closed", "outcome": "false_positive", "closing_note": "benign"})
                assert r.status_code == 200, r.text
                assert (r.json()["status"], r.json()["outcome"], r.json()["closing_note"]) == (
                    "closed", "false_positive", "benign")
                # once closed, the note can't be blanked; reopening is fine (no forced Resolved step either way)
                assert (await c.put(url, json={"closing_note": ""})).status_code == 422
                assert (await c.put(url, json={"status": "open"})).json()["status"] == "open"
                # values already stored on the case satisfy the rule (Open -> Closed directly)
                r = await c.put(f"/api/v1/cases/{stored.id}", json={"status": "closed"})
                assert r.status_code == 200 and r.json()["status"] == "closed", r.text
                # creating a case directly as closed is refused (it can't carry an outcome/closing note)
                r = await c.post("/api/v1/cases/", json={"title": "born closed", "status": "closed"})
                assert r.status_code == 422 and "required to close" in r.json()["detail"], r.text
        finally:
            await db.close()
    run(body)


def test_workflow_case_update_node_must_supply_outcome_and_note():
    async def body(ids):
        db, t = await _setup(ids)
        try:
            case = await make_case(db, ids, t, status=CaseStatus.IN_PROGRESS)
            nctx = NodeContext(db=db, run=SimpleNamespace(tenant_id=t.id, case_id=case.id, depth=0), step=None,
                               node={}, ctx={})
            with pytest.raises(NodeError) as e:
                await run_case_update(nctx, {"status": "closed"})
            assert "required to close a case" in str(e.value)
            with pytest.raises(NodeError):
                await run_case_update(nctx, {"status": "closed", "outcome": "unknown", "closing_note": "  "})
            with pytest.raises(NodeError):
                await run_case_update(nctx, {"status": "closed", "outcome": "bogus", "closing_note": "x"})
            await db.refresh(case)
            assert case.status == CaseStatus.IN_PROGRESS
            out = await run_case_update(nctx, {"status": "closed", "outcome": "true_positive_without_impact",
                                               "closing_note": "contained by {{ automation }}"})
            assert out["changes"]["status"]["to"] == "closed"
            await db.commit()
            await db.refresh(case)
            assert (case.status, case.outcome) == (CaseStatus.CLOSED, "true_positive_without_impact")
        finally:
            await db.close()
    run(body)


def test_apply_case_update_is_the_choke_point():
    """Slack/workflow call apply_case_update directly (no check_case_update): it refuses before touching the case."""
    async def body(ids):
        db, t = await _setup(ids)
        try:
            case = await make_case(db, ids, t, status=CaseStatus.OPEN)
            with pytest.raises(CaseClosureIncomplete):
                await apply_case_update(db, case=case, update_data={"status": CaseStatus.CLOSED, "tags": ["x"]},
                                        user_id=None)
            assert case.status == CaseStatus.OPEN and not case.tags
        finally:
            await db.close()
    run(body)
