from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models.artifact import Artifact
from app.models.case import Case, CaseSeverity, CaseStatus, TimelineEvent
from app.models.case_artifact import CaseArtifact
from app.models.membership import TenantMembership
from app.models.user import User
from app.services.artifact_types import UnknownArtifactType
from app.services.case_service import add_artifact_by_key, add_timeline_note, apply_case_update
from app.utils.audit import create_audit_log
from app.utils.playbooks import apply_playbook_to_case
from app.workflows.events import emit_event
from app.workflows.nodes import NodeContext, NodeError, executor, load_target_case


def _as_list(v) -> list:
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x) for x in v]


def _normalize_email(v) -> str:
    return str(v).strip().lower()


def _emit_updated(nctx: NodeContext, case_id: int, changes: dict) -> None:
    run = nctx.run
    if changes:
        nctx.after_commit.append(lambda: emit_event(run.tenant_id, "case.updated", case_id=case_id,
                                                    changes=changes, depth=run.depth + 1))


@executor("case_update")
async def run_case_update(nctx: NodeContext, config: dict) -> dict:
    case = await load_target_case(nctx, config)
    update = {}
    try:
        if config.get("severity"):
            update["severity"] = CaseSeverity(config["severity"])
        if config.get("status"):
            update["status"] = CaseStatus(config["status"])
    except ValueError as e:
        raise NodeError(str(e))
    if config.get("owner_email"):
        user = (await nctx.db.execute(
            select(User).join(TenantMembership, TenantMembership.user_id == User.id)
            .where(func.lower(User.email) == _normalize_email(config["owner_email"]), TenantMembership.tenant_id == case.tenant_id)
        )).scalars().first()
        if not user:
            raise NodeError(f"no tenant member with email {config['owner_email']}")
        update["owner_id"] = user.id
    add, remove = _as_list(config.get("add_tags")), set(_as_list(config.get("remove_tags")))
    if add or remove:
        tags = [t for t in (case.tags or []) if t not in remove]
        tags += [t for t in add if t not in tags]
        update["tags"] = tags
    changes = await apply_case_update(nctx.db, case=case, update_data=update, user_id=None)
    _emit_updated(nctx, case.id, changes)
    return {"case_id": case.id, "changes": changes}


@executor("case_add_note")
async def run_case_add_note(nctx: NodeContext, config: dict) -> dict:
    case = await load_target_case(nctx, config)
    ev = await add_timeline_note(nctx.db, case=case, content=f"[automation] {config['content']}", user_id=None)
    return {"case_id": case.id, "event_id": ev.id}


@executor("case_add_artifact")
async def run_case_add_artifact(nctx: NodeContext, config: dict) -> dict:
    case = await load_target_case(nctx, config)
    try:
        artifact = await add_artifact_by_key(nctx.db, case=case, key=str(config["artifact_type"]), value=str(config["value"]),
                                             description=config.get("description"), user_id=None)
    except UnknownArtifactType:
        raise NodeError(f"unknown artifact_type {config['artifact_type']!r}")
    return {"case_id": case.id, "artifact_id": artifact.id}


@executor("case_apply_playbook")
async def run_case_apply_playbook(nctx: NodeContext, config: dict) -> dict:
    case = await load_target_case(nctx, config)
    try:
        n = await apply_playbook_to_case(nctx.db, case=case, template_id=int(config["template_id"]), tenant_id=case.tenant_id)
    except HTTPException as e:
        raise NodeError(str(e.detail))
    await create_audit_log(db=nctx.db, entity_type="case", entity_id=case.id, action="playbook_applied",
                           tenant_id=case.tenant_id, user_id=None,
                           changes={"template_id": int(config["template_id"]), "tasks_added": n, "by": "automation"})
    return {"case_id": case.id, "tasks_added": n}


@executor("case_search")
async def run_case_search(nctx: NodeContext, config: dict) -> dict:
    q = select(Case).where(Case.tenant_id == nctx.run.tenant_id)
    statuses = _as_list(config.get("status"))
    if statuses:
        try:
            q = q.where(Case.status.in_([CaseStatus(s) for s in statuses]))
        except ValueError as e:
            raise NodeError(str(e))
    if config.get("title_contains"):
        q = q.where(Case.title.ilike(f"%{config['title_contains']}%"))
    if config.get("created_within_hours"):
        since = datetime.now(timezone.utc) - timedelta(hours=float(config["created_within_hours"]))
        q = q.where(Case.created_at >= since)
    if config.get("artifact_value"):
        q = q.join(CaseArtifact, CaseArtifact.case_id == Case.id).join(Artifact, Artifact.id == CaseArtifact.artifact_id) \
             .where(Artifact.value == str(config["artifact_value"]))
    rows = (await nctx.db.execute(q.order_by(Case.created_at.desc()).limit(200))).scalars().unique().all()
    tags_any = set(_as_list(config.get("tags_any")))
    if tags_any:  # tags is JSON; filter in Python (bounded by limit above)
        rows = [c for c in rows if tags_any & set(c.tags or [])]
    rows = rows[:50]
    cases = [{"id": c.id, "title": c.title, "status": c.status.value, "severity": c.severity.value,
              "tags": c.tags or [], "created_at": c.created_at.isoformat() if c.created_at else None} for c in rows]
    return {"cases": cases, "first": cases[0] if cases else None, "count": len(cases)}


_RESULT_STATUSES = ("ok", "warning", "failed")


def _truthy(v) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() not in ("false", "0", "no", "")


@executor("case_task_complete")
async def run_case_task_complete(nctx: NodeContext, config: dict) -> dict:
    """Record a result on the case task this run was started from (Run on a task), or on the open task named
    `task_title`, and tick it unless `complete` is false. No such task: does nothing, so templates can end with it."""
    from app.models.case_task import CaseTask
    from app.services.task_service import update_case_task
    run = nctx.run
    if not run.case_id:
        return {"task_id": None, "completed": False, "reason": "this run has no case"}
    task = (await nctx.db.execute(select(CaseTask).where(
        CaseTask.case_id == run.case_id, CaseTask.last_run_id == run.id))).scalars().first()
    title = str(config.get("task_title") or "").strip()
    if task is None and title:
        task = (await nctx.db.execute(select(CaseTask).where(
            CaseTask.case_id == run.case_id, func.lower(CaseTask.title) == title.lower())
            .order_by(CaseTask.status.desc(), CaseTask.order))).scalars().first()  # 'todo' before 'done'
    if task is None:
        return {"task_id": None, "completed": False, "reason": "this run was not started from a task"}
    status = str(config.get("result_status") or "ok").strip().lower()
    if status not in _RESULT_STATUSES:
        raise NodeError(f"result_status must be one of {', '.join(_RESULT_STATUSES)}, got {status!r}")
    result = str(config.get("result") or "").strip()[:2000]
    complete = _truthy(config.get("complete", True))
    task.result, task.result_status, task.result_at = result or None, status, datetime.now(timezone.utc)
    case = await load_target_case(nctx, {})
    if result:
        nctx.db.add(TimelineEvent(case_id=case.id, event_type="task",
                                  content=f"Automation result for **{task.title}** ({status}): {result}"))
    if complete and task.status != "done":
        await update_case_task(nctx.db, case=case, task=task, data={"status": "done"}, user_id=None)
    await nctx.db.flush()
    return {"task_id": task.id, "completed": task.status == "done", "result_status": status}
