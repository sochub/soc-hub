import hmac
import logging
from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Text, select
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.v1.workflows import _get_workflow, _secret_names, save_workflow
from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.models.user import User
from app.models.workflow import Workflow
from app.schemas.secret import SecretCreate, SecretNameOut, SecretOut, SecretUpdate
from app.secrets.hosts import host_allowed, valid_host_pattern
from app.secrets.refs import NAME_RE
from app.secrets.scan import apply_conversion, scan_graph
from app.utils.audit import create_audit_log
from app.utils.crypto import decrypt, encrypt
from app.workflows.validation import secret_names_in_graph



class _NoEchoRoute(APIRoute):
    """Strip `input`/`ctx` from 422s on this router: pydantic's `missing` errors echo the whole body, value included."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errs = [{k: v for k, v in e.items() if k not in ("input", "ctx", "url")} for e in exc.errors()]
                return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errs)})
        return wrapped


router = APIRouter(route_class=_NoEchoRoute)
VALUE_ERR = "Secret value must be 1-8192 characters"
logger = logging.getLogger(__name__)


async def _in_use_by(db: AsyncSession, tenant_id: int, name: str) -> List[dict]:
    wfs = (await db.execute(
        select(Workflow).where(Workflow.tenant_id == tenant_id,
                               Workflow.graph.cast(Text).ilike(f"%{name}%"))  # coarse prefilter
        .order_by(Workflow.name))).scalars().all()
    return [{"id": w.id, "name": w.name} for w in wfs if name in secret_names_in_graph(w.graph)]


async def _emails(db: AsyncSession, rows) -> dict:
    ids = {i for r in rows for i in (r.created_by, r.updated_by) if i}
    if not ids:
        return {}
    return dict((await db.execute(select(User.id, User.email).where(User.id.in_(ids)))).all())


def _out(row: TenantSecret, emails: dict, in_use: List[dict]) -> dict:
    return {"name": row.name, "description": row.description, "allowed_hosts": row.allowed_hosts or [],
            "last_used_at": row.last_used_at, "created_at": row.created_at, "updated_at": row.updated_at,
            "created_by_email": emails.get(row.created_by), "updated_by_email": emails.get(row.updated_by),
            "in_use_by": in_use}


async def _get(db: AsyncSession, tenant_id: int, name: str) -> TenantSecret:
    row = (await db.execute(select(TenantSecret).where(
        TenantSecret.tenant_id == tenant_id, TenantSecret.name == name))).scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Secret not found")
    return row


async def _check_hosts(db: AsyncSession, tenant_id: int, hosts: List[str]) -> None:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    allow = (tenant.workflow_http_allowlist if tenant else None) or []
    bad = [h for h in hosts if not valid_host_pattern(h, allow)]
    if bad:
        raise HTTPException(status_code=422, detail=f"Invalid allowed host(s): {', '.join(repr(h) for h in bad)}")


async def _insert(db: AsyncSession, tenant_id: int, user_id: int, name: str, value: str, hosts: List[str],
                  description) -> TenantSecret:
    """Create + flush + audit (no commit). 409 on duplicate name."""
    if not value.strip() or len(value) > 8192:
        raise HTTPException(status_code=422, detail=VALUE_ERR)
    exists = (await db.execute(select(TenantSecret.id).where(
        TenantSecret.tenant_id == tenant_id, TenantSecret.name == name))).first()
    if exists:
        logger.info("secret create tenant=%s name=%s outcome=duplicate", tenant_id, name)
        raise HTTPException(status_code=409, detail="A secret with this name already exists")
    row = TenantSecret(tenant_id=tenant_id, name=name, value_enc=encrypt(value), allowed_hosts=hosts,
                       description=description, created_by=user_id, updated_by=user_id)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        logger.info("secret create tenant=%s name=%s outcome=duplicate", tenant_id, name)
        raise HTTPException(status_code=409, detail="A secret with this name already exists")
    await create_audit_log(db=db, entity_type="secret", entity_id=row.id, action="create", tenant_id=tenant_id,
                           user_id=user_id, changes={"name": row.name, "fields": ["value", "allowed_hosts", "description"]})
    return row


@router.get("/", response_model=List[SecretOut])
async def list_secrets(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    rows = (await db.execute(select(TenantSecret).where(TenantSecret.tenant_id == tenant_id)
                             .order_by(TenantSecret.name))).scalars().all()
    emails = await _emails(db, rows)
    # ponytail: N+1 (one in_use query per secret); fine up to ~100 secrets/tenant, batch it beyond that
    return [_out(r, emails, await _in_use_by(db, tenant_id, r.name)) for r in rows]


@router.get("/names", response_model=List[SecretNameOut])
async def secret_names(db: AsyncSession = Depends(deps.get_db),
                       current_user: User = Depends(deps.require_analyst_or_above),
                       tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    rows = (await db.execute(select(TenantSecret).where(TenantSecret.tenant_id == tenant_id)
                             .order_by(TenantSecret.name))).scalars().all()
    return [{"name": r.name, "description": r.description, "allowed_hosts": r.allowed_hosts or []} for r in rows]


@router.post("/", response_model=SecretOut, status_code=201)
async def create_secret(body: SecretCreate, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    if not body.value.strip() or len(body.value) > 8192:
        raise HTTPException(status_code=422, detail=VALUE_ERR)
    await _check_hosts(db, tenant_id, body.allowed_hosts)
    row = await _insert(db, tenant_id, current_user.id, body.name, body.value, body.allowed_hosts, body.description)
    await db.commit()
    await db.refresh(row)
    logger.info("secret create tenant=%s name=%s outcome=ok", tenant_id, row.name)
    return _out(row, {current_user.id: current_user.email}, [])


@router.put("/{name}", response_model=SecretOut)
async def update_secret(name: str, body: SecretUpdate, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    row = await _get(db, tenant_id, name)
    if body.value is not None and len(body.value) > 8192:
        raise HTTPException(status_code=422, detail=VALUE_ERR)
    fields = []
    if body.allowed_hosts is not None:
        # only newly added entries are revalidated: an unchanged entry stays even if no longer allowlisted
        await _check_hosts(db, tenant_id, [h for h in body.allowed_hosts if h not in (row.allowed_hosts or [])])
        if body.allowed_hosts != (row.allowed_hosts or []):
            row.allowed_hosts = body.allowed_hosts
            fields.append("allowed_hosts")
    if body.description is not None and body.description != row.description:
        row.description = body.description
        fields.append("description")
    if body.value is not None and body.value.strip():
        row.value_enc = encrypt(body.value)
        fields.append("value")
    if fields:
        row.updated_by = current_user.id
        await create_audit_log(db=db, entity_type="secret", entity_id=row.id, action="update", tenant_id=tenant_id,
                               user_id=current_user.id, changes={"name": row.name, "fields": fields})
    await db.commit()
    await db.refresh(row)
    logger.info("secret update tenant=%s name=%s outcome=ok", tenant_id, name)
    return _out(row, await _emails(db, [row]), await _in_use_by(db, tenant_id, name))


@router.delete("/{name}", status_code=204, response_class=Response)
async def delete_secret(name: str, force: bool = False, db: AsyncSession = Depends(deps.get_db),
                        current_user: User = Depends(deps.require_admin),
                        tenant_id: int = Depends(deps.get_effective_tenant_id)):
    row = await _get(db, tenant_id, name)
    in_use = await _in_use_by(db, tenant_id, name)
    if in_use and not force:
        logger.info("secret delete tenant=%s name=%s outcome=in_use", tenant_id, name)
        return JSONResponse(status_code=409, content={
            "detail": f"Secret {name} is used by {len(in_use)} workflow(s)", "workflows": in_use})
    await create_audit_log(db=db, entity_type="secret", entity_id=row.id, action="delete", tenant_id=tenant_id,
                           user_id=current_user.id, changes={"name": name, "fields": []})
    await db.delete(row)
    await db.commit()
    logger.info("secret delete tenant=%s name=%s outcome=ok", tenant_id, name)
    return Response(status_code=204)


NOT_CONVERTIBLE = "add the host to the tenant HTTP allowlist to convert"


def _scan_workflow(wf: Workflow, allow: List[str]) -> List[dict]:
    """All findings; those whose host can't be a secret host pattern are reported but not convertible."""
    out = []
    for f in scan_graph(wf.graph):
        ok = valid_host_pattern(f["host"], allow)
        out.append({**f, "convertible": ok, **({} if ok else {"reason": NOT_CONVERTIBLE})})
    return out


async def _allowlist(db: AsyncSession, tenant_id: int) -> List[str]:
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
    return (tenant.workflow_http_allowlist if tenant else None) or []


@router.get("/scan")
async def scan_workflows(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.require_admin),
                         tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    allow = await _allowlist(db, tenant_id)
    wfs = (await db.execute(select(Workflow).where(Workflow.tenant_id == tenant_id).order_by(Workflow.name))).scalars().all()
    return [{"workflow_id": w.id, "workflow_name": w.name, **{k: v for k, v in f.items() if k != "literal"}}
            for w in wfs for f in _scan_workflow(w, allow)]


class ConvertItem(BaseModel):
    node_id: str
    location: str
    key: str
    secret_name: str


class ConvertIn(BaseModel):
    items: List[ConvertItem]


@router.post("/convert/{workflow_id}")
async def convert_workflow(workflow_id: int, body: ConvertIn, db: AsyncSession = Depends(deps.get_db),
                           current_user: User = Depends(deps.require_admin),
                           tenant_id: int = Depends(deps.get_effective_tenant_id)) -> Any:
    try:
        return await _convert(db, workflow_id, body, current_user, tenant_id)
    except BaseException:
        await db.rollback()  # atomic: no secrets, no workflow change
        raise


def _decrypt_or_409(row: TenantSecret) -> str:
    try:
        return decrypt(row.value_enc)
    except Exception:
        logger.info("secret convert name=%s outcome=undecryptable", row.name)
        raise HTTPException(status_code=409, detail=f"Secret {row.name} could not be decrypted — re-enter it in Integrations") from None


async def _convert(db, workflow_id, body, current_user, tenant_id):
    wf = await _get_workflow(db, workflow_id, tenant_id, lock=True)
    findings = {(f["node_id"], f["location"], f["key"]): f
                for f in _scan_workflow(wf, await _allowlist(db, tenant_id))}
    if not body.items:
        raise HTTPException(status_code=422, detail="No items to convert")
    todo, seen = [], set()
    for it in body.items:
        k = (it.node_id, it.location, it.key)
        f = findings.get(k)
        if f is None or k in seen:
            raise HTTPException(status_code=422, detail=f"No plaintext credential found at node {it.node_id!r} {it.location} {it.key!r}")
        if not f["convertible"]:
            raise HTTPException(status_code=422, detail=f"Host {f['host']} cannot be a secret host — {NOT_CONVERTIBLE}")
        if not NAME_RE.match(it.secret_name):
            raise HTTPException(status_code=422, detail="Invalid secret name")
        seen.add(k)
        todo.append({**f, "secret_name": it.secret_name})
    created, reused = [], []
    for it in todo:
        row = (await db.execute(select(TenantSecret).where(
            TenantSecret.tenant_id == tenant_id, TenantSecret.name == it["secret_name"]))).scalars().first()
        if row is None:
            await _insert(db, tenant_id, current_user.id, it["secret_name"], it["literal"], [it["host"]],
                          "Converted from plaintext workflow credential")
            created.append(it["secret_name"])
        elif hmac.compare_digest(_decrypt_or_409(row).encode(), it["literal"].encode()):
            if not host_allowed(it["host"], row.allowed_hosts or []):
                raise HTTPException(status_code=409, detail=f"Secret {it['secret_name']} is not allowed for host {it['host']} — add the host to the secret or choose another name")
            if it["secret_name"] not in created and it["secret_name"] not in reused:
                reused.append(it["secret_name"])
        else:
            logger.info("secret convert tenant=%s outcome=conflict", tenant_id)
            raise HTTPException(status_code=409, detail=f"Secret {it['secret_name']} already exists with a different value")
    names = await _secret_names(db, tenant_id) | {i["secret_name"] for i in todo}
    await save_workflow(db, wf, name=wf.name, description=wf.description, trigger_type=wf.trigger_type,
                        trigger_filter=wf.trigger_filter, graph=apply_conversion(wf.graph, todo),
                        names=names, tenant_id=tenant_id, user_id=current_user.id)
    await db.commit()
    logger.info(
        "secret convert tenant=%s workflow=%s created_count=%s reused_count=%s outcome=ok",
        tenant_id,
        workflow_id,
        len(created),
        len(reused),
    )
    return {"workflow_id": workflow_id, "version": wf.version, "created": created, "reused": reused}
