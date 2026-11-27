"""Security briefs: the feed list with per-user read/saved state, on-demand AI briefs, and source management.

Every route 404s unless the active organization turned the feature on (`briefs_tenant`).
"""
from datetime import datetime, timezone
from typing import Any, List, Literal, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, cast, exists, func, literal, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import AIUnavailable
from app.api import deps
from app.models.artifact import Artifact
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.ioc import IOC
from app.models.brief import KINDS, BriefAI, BriefItem, BriefSource, BriefUserState
from app.models.tenant import Tenant
from app.models.user import User
from app.services import briefs
from app.services.artifact_types import not_private
from app.utils.audit import create_audit_log

Kind = Literal[KINDS]
UNREAD_BADGE_CAP = 100  # the nav badge shows "99+" past this; counting further is wasted work
MATCH_CASES = 5  # cases listed per matched value


async def briefs_tenant(db: AsyncSession = Depends(deps.get_db),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Tenant:
    """The active organization, or 404 when it has Security briefs off (the feature then does not exist)."""
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or not tenant.briefs_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    return tenant


# On the router so it runs before any role check: with the feature off, a viewer gets the same 404 as an admin
# (not a 403 that would confirm the route exists). Routes that name it as a parameter reuse this one result.
router = APIRouter(dependencies=[Depends(briefs_tenant)])


class SourceRef(BaseModel):
    id: int
    name: str


class BriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    status: str
    what_happened: Optional[str] = None
    why_it_matters: Optional[str] = None
    lessons: List[str] = []
    model: Optional[str] = None
    created_at: Optional[datetime] = None


class CaseRef(BaseModel):
    id: int
    title: str


class Indicator(BaseModel):
    type: str
    value: str


class MatchOut(BaseModel):
    """A value of the brief (indicator or CVE id) that is also one of this organization's observables."""
    value: str
    type: str
    cases: List[CaseRef] = []
    ioc: bool = False


class ItemOut(BaseModel):
    id: int
    title: str
    summary: str
    url: Optional[str] = None
    published_at: datetime
    kind: str
    cves: List[str] = []
    indicators: List[Indicator] = []
    matches: List[MatchOut] = []  # always [] when the organization has correlation off
    source: SourceRef
    brief: Optional[BriefOut] = None
    read: bool
    saved: bool


class StateIn(BaseModel):
    read: Optional[bool] = None
    saved: Optional[bool] = None


class SourceOut(BaseModel):
    id: int
    name: str
    url: str
    kind: str
    format: str
    builtin: bool
    enabled: bool  # for a built-in: whether THIS organization has it on
    last_fetched_at: Optional[datetime] = None
    last_ok_at: Optional[datetime] = None
    last_error: Optional[str] = None
    item_count: int = 0


def _https_url(v: str) -> str:
    v = v.strip()
    try:
        parts = urlsplit(v)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    # Userinfo is refused too: credentials in a feed URL would be stored and shown in clear.
    if not parts or parts.scheme.lower() != "https" or not host or "@" in parts.netloc:
        raise ValueError("must be an https:// URL without credentials")
    return v


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2000)
    kind: Kind

    _url = field_validator("url")(_https_url)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class SourceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    enabled: Optional[bool] = None


class ItemFilters:
    """The list filters, shared by GET / and POST /read-all."""
    def __init__(
        self,
        kind: Optional[Kind] = None,
        source_id: Optional[int] = None,
        q: Optional[str] = Query(None, max_length=200, description="Case-insensitive substring of title or summary."),
        unread: Optional[bool] = None,
        saved: Optional[bool] = None,
        cve: Optional[str] = Query(None, max_length=32, description="Exact CVE id, e.g. CVE-2026-12345."),
        matched: Optional[bool] = Query(None, description=(
            "true = only items with an indicator or CVE that is one of this organization's artifacts or IOCs. "
            "Ignored when the organization has correlation off.")),
    ):
        self.kind, self.source_id, self.q, self.unread, self.saved, self.cve = kind, source_id, q, unread, saved, cve
        self.matched = matched


def _observables(tenant_id: int):
    """Lowercased values of the organization's observables: its artifacts (never private-type ones) and IOCs."""
    return (select(func.lower(Artifact.value)).where(Artifact.tenant_id == tenant_id, not_private())
            .union(select(func.lower(IOC.value)).where(IOC.tenant_id == tenant_id)))


def _matched(tenant_id: int):
    """SQL condition on BriefItem: one of its indicators or CVE ids is an observable of this organization.
    Exact value, case-insensitive (a domain indicator does not match a URL observable on that host)."""
    # ponytail: computed per request, so an observable added today flags last month's brief and nothing is stored
    # per organization. The cost is a scan: every candidate item's values are unnested and compared with
    # lower(value) of the organization's artifacts and IOCs (no index on lower(value)). Fine for 90 days of
    # feed items; past ~100k items or observables, add expression indexes on lower(value) or a match table
    # maintained at ingest and on observable writes.
    ind = func.jsonb_array_elements(func.coalesce(cast(BriefItem.indicators, JSONB), text("'[]'::jsonb"))).table_valued("value")
    cve = func.jsonb_array_elements_text(cast(BriefItem.cves, JSONB)).table_valued("value")
    return or_(
        exists(select(1).select_from(ind).where(func.lower(ind.c.value.op("->>")("value")).in_(_observables(tenant_id)))),
        exists(select(1).select_from(cve).where(func.lower(cve.c.value).in_(_observables(tenant_id)))))


async def _matches(db: AsyncSession, tenant_id: int, items: List[BriefItem]) -> dict:
    """{item id: [MatchOut]} for one page of items, from a single query over the organization's observables."""
    wanted = {str(v).lower() for i in items for v in [*(i.cves or []), *(x.get("value", "") for x in i.indicators or [])]}
    if not wanted:
        return {}
    # Cases are joined on their own tenant_id too, so a mislinked row could never surface another organization's case.
    arts = (select(func.lower(Artifact.value).label("v"), Case.id.label("case_id"), Case.title.label("title"),
                   literal(False).label("ioc"))
            .select_from(Artifact).outerjoin(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
            .outerjoin(Case, and_(Case.id == CaseArtifact.case_id, Case.tenant_id == tenant_id))
            .where(Artifact.tenant_id == tenant_id, not_private(), func.lower(Artifact.value).in_(wanted)))
    iocs = (select(func.lower(IOC.value), Case.id, Case.title, literal(True))
            .select_from(IOC).outerjoin(Case, and_(Case.id == IOC.case_id, Case.tenant_id == tenant_id))
            .where(IOC.tenant_id == tenant_id, func.lower(IOC.value).in_(wanted)))
    found: dict = {}
    for v, case_id, title, ioc in (await db.execute(arts.union_all(iocs))).all():
        hit = found.setdefault(v, {"cases": {}, "ioc": False})
        hit["ioc"] = hit["ioc"] or ioc
        if case_id is not None:
            hit["cases"][case_id] = title
    out = {}
    for i in items:
        values = [("cve", c) for c in i.cves or []] + [(x.get("type", "other"), x.get("value", "")) for x in i.indicators or []]
        out[i.id] = [
            MatchOut(value=value, type=kind, ioc=found[value.lower()]["ioc"],
                     cases=[CaseRef(id=cid, title=t) for cid, t in
                            sorted(found[value.lower()]["cases"].items(), reverse=True)[:MATCH_CASES]])
            for kind, value in values if value.lower() in found]
    return out


def _scoped(stmt, tenant: Tenant, user: User, f: Optional[ItemFilters] = None):
    """Join the source and the caller's state onto a select over BriefItem and keep what this organization sees."""
    stmt = (stmt.select_from(BriefItem).join(BriefSource, BriefSource.id == BriefItem.source_id)
            .outerjoin(BriefUserState, and_(BriefUserState.item_id == BriefItem.id, BriefUserState.user_id == user.id,
                                            BriefUserState.tenant_id == tenant.id))
            .where(briefs.visible(tenant.id, briefs.disabled_ids(tenant.briefs_disabled_sources))))
    if f is None:
        return stmt
    if f.kind:
        stmt = stmt.where(BriefItem.kind == f.kind)
    if f.source_id is not None:
        stmt = stmt.where(BriefItem.source_id == f.source_id)
    if f.q and f.q.strip():
        term = f.q.strip()
        stmt = stmt.where(or_(BriefItem.title.icontains(term, autoescape=True),
                              BriefItem.summary.icontains(term, autoescape=True)))
    if f.unread is not None:
        stmt = stmt.where(BriefUserState.read_at.is_(None) if f.unread else BriefUserState.read_at.isnot(None))
    if f.saved is not None:
        stmt = stmt.where(BriefUserState.saved_at.isnot(None) if f.saved else BriefUserState.saved_at.is_(None))
    if f.cve and f.cve.strip():
        stmt = stmt.where(cast(BriefItem.cves, JSONB).contains([f.cve.strip().upper()]))
    if f.matched and tenant.briefs_correlate:
        stmt = stmt.where(_matched(tenant.id))
    return stmt


def _item_out(item: BriefItem, source_name: str, ai: Optional[BriefAI], state: Optional[BriefUserState],
              matches: List[MatchOut]) -> ItemOut:
    return ItemOut(id=item.id, title=item.title, summary=item.summary or "", url=item.url,
                   published_at=item.published_at, kind=item.kind, cves=item.cves or [],
                   indicators=item.indicators or [], matches=matches,
                   source=SourceRef(id=item.source_id, name=source_name),
                   brief=BriefOut.model_validate(ai) if ai else None,
                   read=bool(state and state.read_at), saved=bool(state and state.saved_at))


async def _visible_item(db: AsyncSession, tenant: Tenant, user: User, item_id: int):
    """(item, source name) or 404: another organization's private item does not exist for the caller."""
    row = (await db.execute(_scoped(select(BriefItem, BriefSource.name), tenant, user)
                            .where(BriefItem.id == item_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Brief not found")
    return row


@router.get("/", response_model=List[ItemOut])
async def list_briefs(
    response: Response,
    f: ItemFilters = Depends(),
    skip: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant: Tenant = Depends(briefs_tenant),
) -> Any:
    """Feed items this organization sees, newest first. The body is a bare list; the total number of matches
    (ignoring skip/limit) is in `X-Total-Count`."""
    total = (await db.execute(select(func.count()).select_from(
        _scoped(select(BriefItem.id), tenant, current_user, f).subquery()))).scalar_one()
    response.headers["X-Total-Count"] = str(total)
    rows = await db.execute(
        _scoped(select(BriefItem, BriefSource.name, BriefAI, BriefUserState), tenant, current_user, f)
        .outerjoin(BriefAI, and_(BriefAI.item_id == BriefItem.id, BriefAI.tenant_id == tenant.id))
        .order_by(BriefItem.published_at.desc(), BriefItem.id.desc()).offset(skip).limit(limit))
    rows = rows.all()
    matches = await _matches(db, tenant.id, [r[0] for r in rows]) if tenant.briefs_correlate else {}
    return [_item_out(*row, matches.get(row[0].id, [])) for row in rows]


@router.get("/unread-count")
async def unread_count(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant: Tenant = Depends(briefs_tenant),
) -> Any:
    """Unread items for the nav badge, and how many of them match the organization's observables (0 when
    correlation is off). Both are counted up to UNREAD_BADGE_CAP."""
    unread = _scoped(select(BriefItem.id), tenant, current_user).where(BriefUserState.read_at.is_(None))

    async def capped(stmt) -> int:
        return (await db.execute(select(func.count()).select_from(stmt.limit(UNREAD_BADGE_CAP).subquery()))).scalar_one()
    n = await capped(unread)
    matched = await capped(unread.where(_matched(tenant.id))) if n and tenant.briefs_correlate else 0
    return {"count": n, "capped": n >= UNREAD_BADGE_CAP, "matched_unread": matched}


@router.post("/read-all")
async def read_all(
    f: ItemFilters = Depends(),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant: Tenant = Depends(briefs_tenant),
) -> Any:
    """Mark every unread item the caller can see (optionally narrowed by the list filters) as read."""
    unread = _scoped(select(BriefItem.id, literal(current_user.id), literal(tenant.id), func.now()),
                     tenant, current_user, f).where(BriefUserState.read_at.is_(None))
    res = await db.execute(
        pg_insert(BriefUserState).from_select(["item_id", "user_id", "tenant_id", "read_at"], unread)
        .on_conflict_do_update(index_elements=["item_id", "user_id", "tenant_id"], set_={"read_at": func.now()}))
    await db.commit()
    return {"marked": res.rowcount or 0}


@router.post("/{item_id}/state")
async def set_state(
    item_id: int,
    body: StateIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    tenant: Tenant = Depends(briefs_tenant),
) -> Any:
    """Set the caller's own read / saved marks on an item. Omitted fields are left as they are."""
    await _visible_item(db, tenant, current_user, item_id)
    now = datetime.now(timezone.utc)
    changes = {col: (now if val else None) for col, val in (("read_at", body.read), ("saved_at", body.saved))
               if val is not None}
    if changes:
        await db.execute(
            pg_insert(BriefUserState)
            .values(item_id=item_id, user_id=current_user.id, tenant_id=tenant.id, **changes)
            .on_conflict_do_update(index_elements=["item_id", "user_id", "tenant_id"], set_=changes))
        await db.commit()
    state = (await db.execute(select(BriefUserState).where(
        BriefUserState.item_id == item_id, BriefUserState.user_id == current_user.id,
        BriefUserState.tenant_id == tenant.id))).scalars().first()
    return {"read": bool(state and state.read_at), "saved": bool(state and state.saved_at)}


@router.post("/{item_id}/brief", response_model=BriefOut)
async def brief_now(
    item_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_analyst_or_above),
    tenant: Tenant = Depends(briefs_tenant),
) -> Any:
    """Write this item's AI brief now (or rewrite a failed one). An item that already has a brief returns it.
    Counts toward the organization's daily cap but is not stopped by it: a person asked for this one."""
    item, source_name = await _visible_item(db, tenant, current_user, item_id)
    existing = (await db.execute(select(BriefAI).where(
        BriefAI.item_id == item_id, BriefAI.tenant_id == tenant.id))).scalars().first()
    if existing is not None and existing.status == "ready":
        return existing
    try:
        return await briefs.generate_brief(db, tenant.id, item, source_name)
    except AIUnavailable:
        # The provider's own reason can expose infrastructure (hosts, account ids): keep it out of the response.
        raise HTTPException(status_code=503, detail="The AI model is unavailable right now. Ask an admin to check "
                                                    "the AI provider settings, then try again.") from None


# --- Sources (admin) --------------------------------------------------------------------------------------------

def _source_out(s: BriefSource, disabled: List[int], item_count: int = 0) -> SourceOut:
    builtin = s.tenant_id is None
    return SourceOut(id=s.id, name=s.name, url=s.url, kind=s.kind, format=s.format, builtin=builtin,
                     enabled=(s.id not in disabled) if builtin else bool(s.enabled),
                     last_fetched_at=s.last_fetched_at, last_ok_at=s.last_ok_at, last_error=s.last_error,
                     item_count=item_count)


async def _manageable_source(db: AsyncSession, tenant: Tenant, source_id: int) -> BriefSource:
    """A built-in the deployment has on, or one of this organization's own; anything else is a 404."""
    s = await db.get(BriefSource, source_id)
    if s is None or (s.tenant_id is None and not s.enabled) or s.tenant_id not in (None, tenant.id):
        raise HTTPException(status_code=404, detail="Source not found")
    return s


@router.get("/sources", response_model=List[SourceOut])
async def list_sources(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant: Tenant = Depends(briefs_tenant),
) -> Any:
    """Built-in sources (with whether this organization has each on) and the organization's own."""
    counts = (select(BriefItem.source_id, func.count().label("n")).group_by(BriefItem.source_id)).subquery()
    rows = await db.execute(
        select(BriefSource, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.source_id == BriefSource.id)
        .where(or_(and_(BriefSource.tenant_id.is_(None), BriefSource.enabled.is_(True)),
                   BriefSource.tenant_id == tenant.id))
        .order_by(BriefSource.tenant_id.isnot(None), BriefSource.id))
    disabled = briefs.disabled_ids(tenant.briefs_disabled_sources)
    return [_source_out(s, disabled, n) for s, n in rows.all()]


@router.post("/sources", response_model=SourceOut, status_code=201)
async def create_source(
    body: SourceCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant: Tenant = Depends(briefs_tenant),
) -> Any:
    """Add an RSS/Atom feed for this organization. The URL is checked against internal addresses at fetch time."""
    n = (await db.execute(select(func.count()).select_from(BriefSource)
                          .where(BriefSource.tenant_id == tenant.id))).scalar_one()
    if n >= briefs.MAX_TENANT_SOURCES:
        raise HTTPException(status_code=400, detail=f"At most {briefs.MAX_TENANT_SOURCES} sources per organization")
    s = BriefSource(tenant_id=tenant.id, name=body.name, url=body.url, kind=body.kind, format="rss",
                    created_by=current_user.id)
    db.add(s)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="This feed URL is already one of your sources") from None
    await create_audit_log(db=db, entity_type="brief_source", entity_id=s.id, action="brief_source.created",
                           tenant_id=tenant.id, user_id=current_user.id,
                           changes={"name": s.name, "url": s.url, "kind": s.kind})
    await db.commit()
    await db.refresh(s)
    return _source_out(s, [])


@router.put("/sources/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: int,
    body: SourceUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant: Tenant = Depends(briefs_tenant),
) -> Any:
    """Rename or enable/disable an own source. For a built-in, `enabled` only switches it on or off for this
    organization; nothing else about a built-in can be changed."""
    s = await _manageable_source(db, tenant, source_id)
    disabled = briefs.disabled_ids(tenant.briefs_disabled_sources)
    changes: dict = {}
    if s.tenant_id is None:
        if body.name is not None:
            raise HTTPException(status_code=400, detail="Built-in sources can only be switched on or off")
        if body.enabled is not None and body.enabled != (s.id not in disabled):
            disabled = [i for i in disabled if i != s.id] + ([] if body.enabled else [s.id])
            tenant.briefs_disabled_sources = disabled  # a new list: JSON columns don't track in-place changes
            changes = {"enabled": body.enabled, "builtin": True}
    else:
        if body.name is not None and body.name.strip() and body.name.strip() != s.name:
            s.name = changes["name"] = body.name.strip()
        if body.enabled is not None and body.enabled != bool(s.enabled):
            s.enabled = changes["enabled"] = body.enabled
    if changes:
        await create_audit_log(db=db, entity_type="brief_source", entity_id=s.id, action="brief_source.updated",
                               tenant_id=tenant.id, user_id=current_user.id, changes={"source": s.name, **changes})
        await db.commit()
    n = (await db.execute(select(func.count()).select_from(BriefItem).where(BriefItem.source_id == s.id))).scalar_one()
    return _source_out(s, disabled, n)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.require_admin),
    tenant: Tenant = Depends(briefs_tenant),
) -> Response:
    """Delete an own source and its items. Built-ins can only be switched off (PUT)."""
    s = await _manageable_source(db, tenant, source_id)
    if s.tenant_id is None:
        raise HTTPException(status_code=400, detail="Built-in sources can only be switched off, not deleted")
    await create_audit_log(db=db, entity_type="brief_source", entity_id=s.id, action="brief_source.deleted",
                           tenant_id=tenant.id, user_id=current_user.id, changes={"name": s.name, "url": s.url})
    await db.delete(s)
    await db.commit()
    return Response(status_code=204)
