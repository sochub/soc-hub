import secrets
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.core.config import settings
from app.escalations import service as svc
from app.escalations.service import EscalationError
from app.models.case import Case
from app.models.escalation import (
    CaseEscalation, EscalationIntegration, EscalationTier, EscalationTierGroup, UserGroup, UserGroupMember,
)
from app.models.membership import TenantMembership
from app.models.user import User
from app.schemas.escalation import (
    EscalateIn, HistoryRow, NoteIn, QueueRow, RedirectIn,
)
from app.schemas.escalation import IntegrationIn, IntegrationOut, OpsgenieTokenOut
from app.schemas.escalation import GroupIn, GroupMemberOut, GroupOut, PolicyIn, PolicyOut, TierOut
from app.utils.account_guard import assert_super_admin_may_bootstrap
from app.utils.audit import create_audit_log
from app.utils.crypto import encrypt

router = APIRouter()


async def _group_out(db: AsyncSession, tenant_id: int, groups: List[UserGroup]) -> List[GroupOut]:
    ids = [g.id for g in groups]
    members: Dict[int, List[GroupMemberOut]] = {i: [] for i in ids}
    tiers: Dict[int, List[int]] = {i: [] for i in ids}
    if ids:
        rows = (await db.execute(
            select(UserGroupMember.group_id, User.id, User.email, User.full_name)
            .join(TenantMembership, TenantMembership.id == UserGroupMember.membership_id)
            .join(User, User.id == TenantMembership.user_id)
            .where(UserGroupMember.group_id.in_(ids), TenantMembership.tenant_id == tenant_id)
            .order_by(User.email))).all()
        for gid, uid, email, name in rows:
            members[gid].append(GroupMemberOut(user_id=uid, email=email, full_name=name))
        for gid, tid in (await db.execute(
                select(EscalationTierGroup.group_id, EscalationTierGroup.tier_id)
                .where(EscalationTierGroup.group_id.in_(ids)).order_by(EscalationTierGroup.tier_id))).all():
            tiers[gid].append(tid)
    return [GroupOut(id=g.id, name=g.name, description=g.description, members=members[g.id], tier_ids=tiers[g.id])
            for g in groups]


async def _get_group(db: AsyncSession, tenant_id: int, group_id: int) -> UserGroup:
    g = (await db.execute(select(UserGroup).where(UserGroup.id == group_id, UserGroup.tenant_id == tenant_id))
         ).scalar_one_or_none()
    if g is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return g


async def _membership_ids(db: AsyncSession, tenant_id: int, user_ids: List[int]) -> List[int]:
    uids = sorted(set(user_ids))
    if not uids:
        return []
    rows = (await db.execute(select(TenantMembership.id, TenantMembership.user_id, TenantMembership.role).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.user_id.in_(uids)))).all()
    if len(rows) != len(uids):
        raise HTTPException(status_code=422, detail="Every member must belong to this organization")
    if any(r.role == "viewer" for r in rows):  # viewers can't accept or act on an escalation
        raise HTTPException(status_code=422, detail="Viewers can't be in escalation groups")
    return [r.id for r in rows]


@router.get("/groups", response_model=List[GroupOut])
async def list_groups(db: AsyncSession = Depends(deps.get_db), tenant_id: int = Depends(deps.get_effective_tenant_id),
                      _user: User = Depends(deps.require_analyst_or_above)):
    groups = (await db.execute(select(UserGroup).where(UserGroup.tenant_id == tenant_id)
                               .order_by(UserGroup.name, UserGroup.id))).scalars().all()
    return await _group_out(db, tenant_id, list(groups))


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(body: GroupIn, request: Request, db: AsyncSession = Depends(deps.get_db),
                       tenant_id: int = Depends(deps.get_effective_tenant_id),
                       user: User = Depends(deps.require_admin)):
    await assert_super_admin_may_bootstrap(db, user, tenant_id, action="escalation_group_create", request=request)
    mids = await _membership_ids(db, tenant_id, body.member_user_ids)
    g = UserGroup(tenant_id=tenant_id, name=body.name, description=body.description)
    db.add(g)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A group with that name already exists")
    db.add_all([UserGroupMember(group_id=g.id, membership_id=m) for m in mids])
    await create_audit_log(db, "user_group", g.id, "user_group_created", tenant_id, user.id,
                           {"name": g.name, "member_user_ids": sorted(set(body.member_user_ids))})
    await db.commit()
    return (await _group_out(db, tenant_id, [g]))[0]


@router.put("/groups/{group_id}", response_model=GroupOut)
async def update_group(group_id: int, body: GroupIn, request: Request, db: AsyncSession = Depends(deps.get_db),
                       tenant_id: int = Depends(deps.get_effective_tenant_id),
                       user: User = Depends(deps.require_admin)):
    await assert_super_admin_may_bootstrap(db, user, tenant_id, action="escalation_group_update", request=request)
    g = await _get_group(db, tenant_id, group_id)
    mids = await _membership_ids(db, tenant_id, body.member_user_ids)
    before = {"name": g.name, "description": g.description}
    g.name, g.description = body.name, body.description
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A group with that name already exists")
    await db.execute(delete(UserGroupMember).where(UserGroupMember.group_id == g.id))
    db.add_all([UserGroupMember(group_id=g.id, membership_id=m) for m in mids])
    await create_audit_log(db, "user_group", g.id, "user_group_updated", tenant_id, user.id,
                           {"before": before, "after": {"name": g.name, "description": g.description,
                                                        "member_user_ids": sorted(set(body.member_user_ids))}})
    await db.commit()
    return (await _group_out(db, tenant_id, [g]))[0]


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(group_id: int, request: Request, db: AsyncSession = Depends(deps.get_db),
                       tenant_id: int = Depends(deps.get_effective_tenant_id),
                       user: User = Depends(deps.require_admin)):
    await assert_super_admin_may_bootstrap(db, user, tenant_id, action="escalation_group_delete", request=request)
    g = await _get_group(db, tenant_id, group_id)
    tier_names = (await db.execute(
        select(EscalationTier.name).join(EscalationTierGroup, EscalationTierGroup.tier_id == EscalationTier.id)
        .where(EscalationTierGroup.group_id == g.id, EscalationTier.tenant_id == tenant_id)
        .order_by(EscalationTier.position))).scalars().all()
    if tier_names:
        raise HTTPException(status_code=409, detail=f"Group is used by tier(s): {', '.join(tier_names)}")
    pending = (await db.execute(select(CaseEscalation.id).where(
        CaseEscalation.tenant_id == tenant_id, CaseEscalation.to_group_id == g.id,
        CaseEscalation.status == "pending").limit(1))).first()
    if pending:
        raise HTTPException(status_code=409, detail="Group has a pending escalation")
    await create_audit_log(db, "user_group", g.id, "user_group_deleted", tenant_id, user.id, {"name": g.name})
    await db.delete(g)
    await db.commit()
    return Response(status_code=204)


async def _policy(db: AsyncSession, tenant_id: int) -> PolicyOut:
    tiers = (await db.execute(select(EscalationTier).where(EscalationTier.tenant_id == tenant_id)
                              .order_by(EscalationTier.position, EscalationTier.id))).scalars().all()
    gids: Dict[int, List[int]] = {t.id: [] for t in tiers}
    if gids:
        for tid, gid in (await db.execute(select(EscalationTierGroup.tier_id, EscalationTierGroup.group_id)
                                          .where(EscalationTierGroup.tier_id.in_(list(gids)))
                                          .order_by(EscalationTierGroup.group_id))).all():
            gids[tid].append(gid)
    return PolicyOut(tiers=[TierOut(id=t.id, position=t.position, name=t.name,
                                    accept_timeout_minutes=t.accept_timeout_minutes, notify_email=t.notify_email,
                                    notify_slack=t.notify_slack, slack_channel=t.slack_channel,
                                    group_ids=gids[t.id]) for t in tiers])


@router.get("/policy", response_model=PolicyOut)
async def get_policy(db: AsyncSession = Depends(deps.get_db), tenant_id: int = Depends(deps.get_effective_tenant_id),
                     _user: User = Depends(deps.require_analyst_or_above)):
    return await _policy(db, tenant_id)


@router.put("/policy", response_model=PolicyOut)
async def put_policy(body: PolicyIn, request: Request, db: AsyncSession = Depends(deps.get_db),
                     tenant_id: int = Depends(deps.get_effective_tenant_id),
                     user: User = Depends(deps.require_admin)):
    await assert_super_admin_may_bootstrap(db, user, tenant_id, action="escalation_policy_update", request=request)
    wanted = {gid for t in body.tiers for gid in t.group_ids}
    if wanted:
        found = set((await db.execute(select(UserGroup.id).where(
            UserGroup.tenant_id == tenant_id, UserGroup.id.in_(wanted)))).scalars().all())
        if found != wanted:
            raise HTTPException(status_code=422, detail="Unknown group id")
    existing = {t.id: t for t in (await db.execute(select(EscalationTier).where(
        EscalationTier.tenant_id == tenant_id))).scalars().all()}
    keep = [t.id for t in body.tiers if t.id is not None]
    if len(set(keep)) != len(keep) or any(i not in existing for i in keep):
        raise HTTPException(status_code=422, detail="Unknown tier id")
    before = (await _policy(db, tenant_id)).model_dump()
    for tid, t in existing.items():
        if tid not in keep:
            await db.delete(t)
    await db.flush()
    for pos, spec in enumerate(body.tiers, start=1):
        tier = existing.get(spec.id) if spec.id is not None else None
        if tier is None:
            tier = EscalationTier(tenant_id=tenant_id)
            db.add(tier)
        tier.position, tier.name = pos, spec.name
        tier.accept_timeout_minutes = spec.accept_timeout_minutes
        tier.notify_email, tier.notify_slack = spec.notify_email, spec.notify_slack
        tier.slack_channel = spec.slack_channel
        await db.flush()
        await db.execute(delete(EscalationTierGroup).where(EscalationTierGroup.tier_id == tier.id))
        db.add_all([EscalationTierGroup(tier_id=tier.id, group_id=g) for g in sorted(set(spec.group_ids))])
    await db.flush()
    after = (await _policy(db, tenant_id)).model_dump()
    await create_audit_log(db, "escalation_policy", tenant_id, "escalation_policy_updated", tenant_id, user.id,
                           {"before": before, "after": after})
    await db.commit()
    return await _policy(db, tenant_id)


# ── Case escalation actions ──────────────────────────────────────

async def _case(db: AsyncSession, tenant_id: int, case_id: int) -> Case:
    case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == tenant_id))).scalars().first()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def _act(db: AsyncSession, case: Case, coro) -> HistoryRow:
    """Run a service transition and commit; a refused one is rolled back whole (e.g. a failed supersede)."""
    try:
        row = await coro
        await db.commit()
    except EscalationError as e:
        await db.rollback()
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    return next(h for h in await svc.history(db, case) if h["id"] == row.id)


@router.post("/cases/{case_id}/escalate", response_model=HistoryRow)
async def escalate_case(case_id: int, body: EscalateIn, db: AsyncSession = Depends(deps.get_db),
                        tenant_id: int = Depends(deps.get_effective_tenant_id),
                        user: User = Depends(deps.require_analyst_or_above)):
    case = await _case(db, tenant_id, case_id)
    return await _act(db, case, svc.escalate(
        db, case=case, actor_id=user.id, to_tier_id=body.to_tier_id, to_group_id=body.to_group_id,
        reason=body.reason, summary=body.summary, requested_help=body.requested_help))


@router.post("/cases/{case_id}/accept", response_model=HistoryRow)
async def accept_case(case_id: int, db: AsyncSession = Depends(deps.get_db),
                      tenant_id: int = Depends(deps.get_effective_tenant_id),
                      user: User = Depends(deps.require_analyst_or_above)):
    case = await _case(db, tenant_id, case_id)
    is_admin = deps._active_role(user) in ("admin", "super_admin")
    return await _act(db, case, svc.accept(db, case=case, actor_id=user.id, is_admin=is_admin))


@router.post("/cases/{case_id}/redirect", response_model=HistoryRow)
async def redirect_case(case_id: int, body: RedirectIn, db: AsyncSession = Depends(deps.get_db),
                        tenant_id: int = Depends(deps.get_effective_tenant_id),
                        user: User = Depends(deps.require_analyst_or_above)):
    case = await _case(db, tenant_id, case_id)
    return await _act(db, case, svc.redirect(db, case=case, actor_id=user.id, to_group_id=body.to_group_id,
                                             note=body.note))


@router.post("/cases/{case_id}/return", response_model=HistoryRow)
async def return_case(case_id: int, body: NoteIn, db: AsyncSession = Depends(deps.get_db),
                      tenant_id: int = Depends(deps.get_effective_tenant_id),
                      user: User = Depends(deps.require_analyst_or_above)):
    case = await _case(db, tenant_id, case_id)
    return await _act(db, case, svc.return_down(db, case=case, actor_id=user.id, note=body.note or ""))


@router.post("/cases/{case_id}/withdraw", response_model=HistoryRow)
async def withdraw_case(case_id: int, body: NoteIn, db: AsyncSession = Depends(deps.get_db),
                        tenant_id: int = Depends(deps.get_effective_tenant_id),
                        user: User = Depends(deps.require_analyst_or_above)):
    case = await _case(db, tenant_id, case_id)
    return await _act(db, case, svc.withdraw(db, case=case, actor_id=user.id, note=body.note))


@router.get("/cases/{case_id}/history", response_model=List[HistoryRow])
async def case_history(case_id: int, db: AsyncSession = Depends(deps.get_db),
                       tenant_id: int = Depends(deps.get_effective_tenant_id),
                       _user: User = Depends(deps.require_analyst_or_above)):
    return await svc.history(db, await _case(db, tenant_id, case_id))


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@router.get("/queue", response_model=List[QueueRow])
async def queue(db: AsyncSession = Depends(deps.get_db), tenant_id: int = Depends(deps.get_effective_tenant_id),
                user: User = Depends(deps.require_analyst_or_above)):
    """Pending escalations waiting on a group the caller belongs to, most severe then most overdue first."""
    mine = select(UserGroupMember.group_id).join(
        TenantMembership, TenantMembership.id == UserGroupMember.membership_id).where(
        TenantMembership.user_id == user.id, TenantMembership.tenant_id == tenant_id)
    rows = (await db.execute(
        select(CaseEscalation, Case.title, Case.severity).join(Case, Case.id == CaseEscalation.case_id)
        .where(CaseEscalation.tenant_id == tenant_id, Case.tenant_id == tenant_id,
               CaseEscalation.status == "pending", CaseEscalation.to_group_id.in_(mine)))).all()
    names = await svc._names(db, [r.requested_by for r, _, _ in rows])
    far = datetime.max.replace(tzinfo=timezone.utc)
    out = []
    for r, title, sev in rows:
        sev = getattr(sev, "value", sev)
        s = svc._summary(r, names)
        out.append((_SEVERITY_RANK.get(sev, 5), r.due_at or far, r.id, QueueRow(
            id=r.id, case_id=r.case_id, case_title=title, severity=sev, tier_position=s["tier_position"],
            tier_name=s["tier_name"], group_id=s["group_id"], group_name=s["group_name"],
            requested_by_name=s["requested_by_name"], created_at=r.created_at, due_at=r.due_at)))
    return [q for *_, q in sorted(out, key=lambda t: t[:3])]


# ── External paging integration (inbound acks) ──────────────────

async def _integration(db: AsyncSession, tenant_id: int) -> EscalationIntegration:
    """The tenant's row, created with a fresh opaque inbound key on first use."""
    q = select(EscalationIntegration).where(EscalationIntegration.tenant_id == tenant_id)
    integ = (await db.execute(q)).scalars().first()
    if integ is not None:
        return integ
    try:
        async with db.begin_nested():  # a concurrent first GET trips the tenant_id unique; reuse its row
            integ = EscalationIntegration(tenant_id=tenant_id, inbound_key=secrets.token_urlsafe(32))
            db.add(integ)
            await db.flush()
    except IntegrityError:
        integ = (await db.execute(q)).scalars().one()
    return integ


def _integration_out(integ: EscalationIntegration) -> IntegrationOut:
    base = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/api/v1/escalations/inbound/{integ.inbound_key}"
    return IntegrationOut(pagerduty_url=f"{base}/pagerduty", opsgenie_url=f"{base}/opsgenie",
                          has_pagerduty_webhook_secret=bool(integ.pagerduty_webhook_secret_enc),
                          has_pagerduty_api_key=bool(integ.pagerduty_api_key_enc),
                          has_opsgenie_token=bool(integ.opsgenie_token_enc))


@router.get("/integrations", response_model=IntegrationOut)
async def get_integrations(db: AsyncSession = Depends(deps.get_db),
                           tenant_id: int = Depends(deps.get_effective_tenant_id),
                           _user: User = Depends(deps.require_admin)):
    integ = await _integration(db, tenant_id)
    await db.commit()
    return _integration_out(integ)


@router.put("/integrations", response_model=IntegrationOut)
async def put_integrations(body: IntegrationIn, request: Request, db: AsyncSession = Depends(deps.get_db),
                           tenant_id: int = Depends(deps.get_effective_tenant_id),
                           user: User = Depends(deps.require_admin)):
    await assert_super_admin_may_bootstrap(db, user, tenant_id, action="escalation_integration_update",
                                           request=request)
    integ = await _integration(db, tenant_id)
    changed = {}
    for field in ("pagerduty_webhook_secret", "pagerduty_api_key"):
        value = getattr(body, field)
        if value is None:
            continue
        value = value.strip()
        setattr(integ, f"{field}_enc", encrypt(value) if value else None)
        changed[field] = "set" if value else "cleared"  # never the value itself
    if changed:
        await create_audit_log(db, "escalation_integration", integ.id, "escalation_integration_updated",
                               tenant_id, user.id, changed)
    await db.commit()
    return _integration_out(integ)


@router.post("/integrations/opsgenie-token", response_model=OpsgenieTokenOut)
async def regenerate_opsgenie_token(request: Request, db: AsyncSession = Depends(deps.get_db),
                                    tenant_id: int = Depends(deps.get_effective_tenant_id),
                                    user: User = Depends(deps.require_admin)):
    """A new random token for Opsgenie's custom header, shown once; the previous token stops working."""
    await assert_super_admin_may_bootstrap(db, user, tenant_id, action="escalation_integration_update",
                                           request=request)
    integ = await _integration(db, tenant_id)
    token = secrets.token_urlsafe(32)
    integ.opsgenie_token_enc = encrypt(token)
    await create_audit_log(db, "escalation_integration", integ.id, "escalation_integration_opsgenie_token_rotated",
                           tenant_id, user.id, {})
    await db.commit()
    return OpsgenieTokenOut(token=token)
