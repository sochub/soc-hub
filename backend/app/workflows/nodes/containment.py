from app.db.session import AsyncSessionLocal
from app.models.case import TimelineEvent
from app.models.containment import TARGET_KINDS, ContainmentAction
from app.services import containment
from app.utils.audit import create_audit_log
from app.workflows.nodes import NodeContext, NodeError, executor


@executor("containment_guard")
async def run_containment_guard(nctx: NodeContext, config: dict) -> dict:
    """Place before a containment call. Allowed: records the action and the run goes on. Refused by the tenant's
    policy: the refusal is recorded (in its own transaction, the failed step's is rolled back) and the run stops."""
    kind = str(config.get("target_kind") or "").strip().lower()
    target = str(config.get("target") or "").strip()[:500]
    action = str(config.get("action") or "containment").strip()[:200]
    if kind not in TARGET_KINDS:
        raise NodeError(f"target_kind must be one of {', '.join(TARGET_KINDS)}")
    if not target:
        raise NodeError("containment target is empty")
    run = nctx.run
    reason = await containment.check(nctx.db, tenant_id=run.tenant_id, case_id=run.case_id, kind=kind, target=target)
    fields = dict(tenant_id=run.tenant_id, case_id=run.case_id, run_id=run.id if run.id and run.id > 0 else None,
                  workflow_id=getattr(run, "workflow_id", None), action=action, target_kind=kind, target=target,
                  release_hint=(config.get("release_hint") or None))
    if getattr(run, "is_dry_run", False):  # check only: nothing is recorded
        if reason:
            raise NodeError(f"containment refused: {reason}")
        return {"allowed": True, "action_id": None, "target": target}
    if reason:
        async with AsyncSessionLocal() as db:  # survives the rollback of this failed step
            row = ContainmentAction(**fields, status="blocked", reason=reason)
            db.add(row)
            if run.case_id:
                db.add(TimelineEvent(case_id=run.case_id, event_type="containment_blocked",
                                     content=f"Automated containment refused ({action} on {kind} {target}): {reason}."))
            await db.flush()
            await create_audit_log(db=db, entity_type="containment_action", entity_id=row.id, action="blocked",
                                   tenant_id=run.tenant_id, actor="automation", changes={"target": target, "reason": reason})
            await db.commit()
        raise NodeError(f"containment refused: {reason}")
    row = ContainmentAction(**fields, status="active")
    nctx.db.add(row)
    if run.case_id:
        nctx.db.add(TimelineEvent(case_id=run.case_id, event_type="containment",
                                  content=f"Automated containment: {action} on {kind} {target}."))
    await nctx.db.flush()
    await create_audit_log(db=nctx.db, entity_type="containment_action", entity_id=row.id, action="contained",
                           tenant_id=run.tenant_id, actor="automation", changes={"target": target, "action": action})
    return {"allowed": True, "action_id": row.id, "target": target}
