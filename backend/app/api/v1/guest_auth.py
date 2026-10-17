"""Guest authentication: magic link, optional account (password + TOTP), reset. SSO lives in sso.py."""
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.v1.mfa import LOGIN_EXPIRED, get_mfa_throttle, get_redis, spend_mfa_challenge, verify_totp
from app.core import security, totp
from app.core.config import settings
from app.core.login_throttle import LoginThrottle, client_ip, get_login_throttle
from app.core.passwords import validate_password_strength
from app.guests import tokens
from app.guests.access import ACTIVE, GuestSession, domain_allowed, get_current_guest
from app.models.case_guest import CaseGuest, GuestAccount
from app.models.tenant import Tenant
from app.services.email_service import send_guest_register_email, send_guest_reset_email
from app.utils.audit import create_audit_log
from app.utils.crypto import encrypt
from app.utils.emails import normalize_email

router = APIRouter()
logger = logging.getLogger(__name__)
_DUMMY_HASH = security.get_password_hash(secrets.token_urlsafe(32))
_EMAIL_RE = re.compile(r"^[^@\s|]+@[^@\s|]+$")
BAD_LOGIN = "Incorrect email or password"
BAD_LINK = "This link is invalid or has expired"

_guest_throttle = get_login_throttle().namespaced("guest")


def get_guest_throttle() -> LoginThrottle:
    """Separate counters from analyst login; tests override it."""
    return _guest_throttle


class LinkIn(BaseModel):
    token: str = Field(max_length=4096)


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(max_length=4096)
    code: str = Field(max_length=16)


class EmailIn(BaseModel):
    email: str = Field(max_length=320)


class ResetIn(BaseModel):
    token: str = Field(max_length=4096)
    password: str = Field(max_length=1024)


class PasswordCodeIn(BaseModel):
    password: str = Field(max_length=1024)
    code: str = Field(max_length=16)


def _token(tok: str) -> dict:
    return {"access_token": tok, "token_type": "bearer"}


def _check_password(pw: str) -> None:
    try:
        validate_password_strength(pw)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


async def _account_by_email(db, email: str) -> Optional[GuestAccount]:
    return (await db.execute(select(GuestAccount).where(GuestAccount.email == email))).scalars().first()


async def _account_from_session(db, s: GuestSession) -> GuestAccount:
    if s.account_id is None:
        raise HTTPException(status_code=403, detail="Sign in with your guest password first")
    return await db.get(GuestAccount, s.account_id, with_for_update=True)


async def _audit_grants(db, email: str, action: str) -> None:
    rows = (await db.execute(select(CaseGuest.id, CaseGuest.tenant_id).where(CaseGuest.email == email))).all()
    for gid, tid in rows:
        await create_audit_log(db=db, entity_type="case_guest", entity_id=gid, action=action, tenant_id=tid,
                               changes={"actor": f"guest:{email}"})


@router.post("/link")
async def link_login(body: LinkIn, db: AsyncSession = Depends(deps.get_db)) -> Any:
    try:
        grant_id = int(tokens.decode(body.token, tokens.LINK)["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail=BAD_LINK)
    row = (await db.execute(select(CaseGuest, Tenant).join(Tenant, Tenant.id == CaseGuest.tenant_id)
                            .where(CaseGuest.id == grant_id, *ACTIVE()))).first()
    if row is None or not domain_allowed(row[1], row[0].email):
        raise HTTPException(status_code=401, detail=BAD_LINK)
    grant, tenant = row
    if tenant.guest_require_sso:
        raise HTTPException(status_code=403, detail={"code": "sso_required", "slug": tenant.slug})
    if await _account_by_email(db, grant.email):  # a link must not bypass the account's password/MFA
        raise HTTPException(status_code=403, detail={"code": "account_login_required"})
    await create_audit_log(db=db, entity_type="case_guest", entity_id=grant.id, action="guest_link_login",
                           tenant_id=tenant.id, changes={"actor": f"guest:{grant.email}"})
    await db.commit()
    return _token(tokens.issue_session(grant.email, "link", link_tenant_id=tenant.id))


@router.post("/register", status_code=202)
async def register(request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(deps.get_db),
                   s: GuestSession = Depends(get_current_guest),
                   throttle: LoginThrottle = Depends(get_guest_throttle)) -> Response:
    """Emails the session's address a confirmation link; the account is only created on confirm, so a
    forwarded invite cannot claim the email's guest identity. Same 202 whether or not an account exists."""
    if s.amr != "link":
        raise HTTPException(status_code=403, detail="Open your invitation link to create an account")
    if not await throttle.reserve(f"reg:{s.email}", client_ip(request)) and not await _account_by_email(db, s.email):
        base = settings.FRONTEND_URL.rstrip("/")
        background_tasks.add_task(send_guest_register_email, s.email,
                                  f"{base}/guest/register#{tokens.issue_register(s.email)}")
    return Response(status_code=202)


@router.post("/register/confirm")
async def register_confirm(body: ResetIn, db: AsyncSession = Depends(deps.get_db)) -> Any:
    bad = HTTPException(status_code=400, detail=BAD_LINK)
    try:
        email = normalize_email(tokens.decode(body.token, tokens.REGISTER)["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError, AttributeError):
        raise bad
    if not _EMAIL_RE.match(email):
        raise bad
    _check_password(body.password)
    if await _account_by_email(db, email):  # the token is spent once the account exists
        raise bad
    acct = GuestAccount(email=email, password_hash=security.get_password_hash(body.password))
    db.add(acct)
    try:
        await db.flush()
    except IntegrityError:  # concurrent confirm of the same token
        await db.rollback()
        raise bad
    await db.execute(update(CaseGuest).where(CaseGuest.email == email).values(guest_account_id=acct.id))
    await _audit_grants(db, email, "guest_account_created")
    await db.commit()
    return _token(tokens.issue_session(email, "password", gav=acct.token_version or 0))


@router.post("/login")
async def login(request: Request, body: LoginIn, db: AsyncSession = Depends(deps.get_db),
                throttle: LoginThrottle = Depends(get_guest_throttle)) -> Any:
    email, ip = normalize_email(body.email), client_ip(request)
    bad = HTTPException(status_code=401, detail=BAD_LOGIN)
    if len(email) > 320 or not _EMAIL_RE.match(email):
        raise bad
    retry = await throttle.reserve(email, ip)
    if retry:
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.",
                            headers={"Retry-After": str(retry)})
    acct = await _account_by_email(db, email)
    ok = security.verify_password(body.password, acct.password_hash if acct else _DUMMY_HASH)
    if not acct or not ok:
        logger.warning("failed guest login ip=%s", ip)
        raise bad
    await throttle.reset(email, ip)
    if acct.mfa_enabled_at is not None:
        return {"mfa_required": True, "mfa_token": tokens.issue_mfa_challenge(acct)}
    await _audit_grants(db, email, "guest_password_login")
    await db.commit()
    return _token(tokens.issue_session(email, "password", gav=acct.token_version or 0))


@router.post("/login/mfa")
async def login_mfa(request: Request, body: MfaLoginIn, db: AsyncSession = Depends(deps.get_db),
                    redis=Depends(get_redis), throttle: LoginThrottle = Depends(get_mfa_throttle)) -> Any:
    expired = HTTPException(status_code=401, detail=LOGIN_EXPIRED)
    try:
        p = tokens.decode(body.mfa_token, tokens.MFA)
        acct = await db.get(GuestAccount, int(p["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise expired
    if acct is None or acct.mfa_enabled_at is None or p.get("tv") != (acct.token_version or 0):
        raise expired
    await verify_totp(redis, throttle, f"g{acct.id}", acct.mfa_secret_enc, body.code, client_ip(request))
    if not await spend_mfa_challenge(redis, p.get("jti")):
        raise expired
    await _audit_grants(db, acct.email, "guest_mfa_login")
    await db.commit()
    return _token(tokens.issue_session(acct.email, "password", gav=acct.token_version or 0))


@router.post("/forgot", status_code=202)
async def forgot(request: Request, body: EmailIn, background_tasks: BackgroundTasks, db: AsyncSession = Depends(deps.get_db),
                 throttle: LoginThrottle = Depends(get_guest_throttle)) -> Response:
    email = normalize_email(body.email)
    if _EMAIL_RE.match(email) and not await throttle.reserve(f"reset:{email}", client_ip(request)):
        acct = await _account_by_email(db, email)
        if acct is not None:
            base = settings.FRONTEND_URL.rstrip("/")
            background_tasks.add_task(send_guest_reset_email, email, f"{base}/guest/reset#{tokens.issue_reset(acct)}")
    return Response(status_code=202)


@router.post("/reset", status_code=204)
async def reset(body: ResetIn, db: AsyncSession = Depends(deps.get_db)) -> Response:
    bad = HTTPException(status_code=400, detail="This reset link is invalid or has expired")
    try:
        p = tokens.decode(body.token, tokens.RESET)
        acct = await db.get(GuestAccount, int(p["sub"]), with_for_update=True)
    except (jwt.PyJWTError, KeyError, ValueError):
        raise bad
    if acct is None or p.get("tv") != (acct.token_version or 0):
        raise bad
    _check_password(body.password)
    acct.password_hash = security.get_password_hash(body.password)
    acct.token_version = (acct.token_version or 0) + 1  # single-use link + logs out every password session
    await _audit_grants(db, acct.email, "guest_password_reset")
    await db.commit()
    return Response(status_code=204)


async def _verify_current_password(request: Request, acct: GuestAccount, password: str,
                                   pw_throttle: LoginThrottle) -> str:
    ip = client_ip(request)
    retry = await pw_throttle.reserve(f"pw:g{acct.id}", ip)
    if retry:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry)})
    if not security.verify_password(password, acct.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect password")
    await pw_throttle.reset(f"pw:g{acct.id}", ip)
    return ip


@router.post("/mfa/setup")
async def mfa_setup(db: AsyncSession = Depends(deps.get_db), s: GuestSession = Depends(get_current_guest)) -> Any:
    acct = await _account_from_session(db, s)
    if acct.mfa_enabled_at is not None:
        raise HTTPException(status_code=409, detail="Two-factor authentication is already enabled")
    secret = totp.new_secret()
    acct.mfa_secret_enc = encrypt(secret)
    await db.commit()
    return {"otpauth_uri": totp.provisioning_uri(secret, acct.email), "secret": secret}


@router.post("/mfa/enable")
async def mfa_enable(request: Request, body: PasswordCodeIn, db: AsyncSession = Depends(deps.get_db),
                     s: GuestSession = Depends(get_current_guest), redis=Depends(get_redis),
                     throttle: LoginThrottle = Depends(get_mfa_throttle),
                     pw_throttle: LoginThrottle = Depends(get_guest_throttle)) -> Any:
    acct = await _account_from_session(db, s)
    if acct.mfa_enabled_at is not None:
        raise HTTPException(status_code=409, detail="Two-factor authentication is already enabled")
    if not acct.mfa_secret_enc:
        raise HTTPException(status_code=400, detail="Start setup first")
    # the password too: a stolen session alone must not be able to lock the owner out
    ip = await _verify_current_password(request, acct, body.password, pw_throttle)
    await verify_totp(redis, throttle, f"g{acct.id}", acct.mfa_secret_enc, body.code, ip)
    acct.mfa_enabled_at = datetime.now(timezone.utc)
    acct.token_version = (acct.token_version or 0) + 1
    await _audit_grants(db, acct.email, "guest_mfa_enabled")
    await db.commit()
    return _token(tokens.issue_session(acct.email, "password", gav=acct.token_version))


@router.post("/mfa/disable")
async def mfa_disable(request: Request, body: PasswordCodeIn, db: AsyncSession = Depends(deps.get_db),
                      s: GuestSession = Depends(get_current_guest), redis=Depends(get_redis),
                      throttle: LoginThrottle = Depends(get_mfa_throttle),
                      pw_throttle: LoginThrottle = Depends(get_guest_throttle)) -> Any:
    acct = await _account_from_session(db, s)
    if acct.mfa_enabled_at is None:
        raise HTTPException(status_code=409, detail="Two-factor authentication is not enabled")
    ip = await _verify_current_password(request, acct, body.password, pw_throttle)
    await verify_totp(redis, throttle, f"g{acct.id}", acct.mfa_secret_enc, body.code, ip)
    acct.mfa_enabled_at, acct.mfa_secret_enc = None, None
    acct.token_version = (acct.token_version or 0) + 1
    await _audit_grants(db, acct.email, "guest_mfa_disabled")
    await db.commit()
    return _token(tokens.issue_session(acct.email, "password", gav=acct.token_version))
