from typing import Optional

from sqlalchemy import select

from app.models.artifact import Artifact
from app.models.case import Alert, Case
from app.models.case_artifact import CaseArtifact
from app.models.workflow import WorkflowRun, WorkflowRunStep


def _enum(v):
    return getattr(v, "value", v)


async def case_to_dict(db, case: Optional[Case]) -> Optional[dict]:
    if case is None:
        return None
    arts = (await db.execute(
        select(Artifact.artifact_type, Artifact.value)
        .join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(CaseArtifact.case_id == case.id)
    )).all()
    return {
        "id": case.id, "title": case.title, "description": case.description,
        "status": _enum(case.status), "severity": _enum(case.severity),
        "tags": list(case.tags or []), "source": case.source, "owner_id": case.owner_id,
        "group_key": case.group_key,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "artifacts": [{"type": _enum(t), "value": v} for t, v in arts],
    }


def alert_to_dict(alert: Optional[Alert]) -> Optional[dict]:
    if alert is None:
        return None
    return {"id": alert.id, "source": alert.source, "external_id": alert.external_id,
            "title": alert.title, "payload": alert.payload, "status": alert.status, "case_id": alert.case_id}


async def _step_outputs(db, run_id: int) -> dict:
    rows = (await db.execute(select(WorkflowRunStep).where(WorkflowRunStep.run_id == run_id))).scalars().all()
    return {s.node_id: {"output": s.output, "status": s.status} for s in rows}


async def build_context(db, run: WorkflowRun) -> dict:
    case = None
    if run.case_id:
        case = (await db.execute(select(Case).where(Case.id == run.case_id, Case.tenant_id == run.tenant_id))).scalars().first()
    alert = None
    if run.alert_id:
        alert = (await db.execute(select(Alert).where(Alert.id == run.alert_id, Alert.tenant_id == run.tenant_id))).scalars().first()
    elif case is not None:
        # Case-scoped runs (manual, case.*) still get the case's originating alert. ponytail: latest wins if several.
        alert = (await db.execute(select(Alert).where(Alert.case_id == case.id, Alert.tenant_id == run.tenant_id)
                                  .order_by(Alert.id.desc()).limit(1))).scalars().first()

    steps = {}
    if run.parent_run_id:  # loop child: parent's outputs are visible too
        steps.update(await _step_outputs(db, run.parent_run_id))
    steps.update(await _step_outputs(db, run.id))

    ctx = {"case": await case_to_dict(db, case), "alert": alert_to_dict(alert),
           "trigger": run.trigger_payload or {}, "steps": steps, "dry_run": run.is_dry_run}
    if run.parent_run_id:
        ctx["loop"] = {"item": run.loop_item, "index": run.loop_index}
    return ctx
