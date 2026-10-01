"""Bedrock credentials.

Deployment default (and auth_mode "chain"): pass no keys — boto3's standard
credential chain applies (env → shared config/profile → web identity/IRSA →
ECS task role → EC2 instance profile).
Tenant "role": the server (using its chain credentials) calls sts:AssumeRole
into the tenant's account with an ExternalId; temporary credentials are cached
until 5 minutes before they expire. Tenant "keys": stored access keys.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.ai.errors import AIUnavailable
from app.ai.providers import ProviderConfig

logger = logging.getLogger(__name__)

_REFRESH_MARGIN = timedelta(minutes=5)
_DURATION_SECONDS = 3600
_NEGATIVE_TTL = timedelta(minutes=10)
_STS_CONFIG = Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1})

_cache: Dict[Tuple[int, str, str], Tuple[dict, datetime]] = {}
_principal: Optional[str] = None
_principal_failed_at: Optional[datetime] = None


def clear_cache(tenant_id: Optional[int] = None) -> None:
    global _principal, _principal_failed_at
    if tenant_id is None:
        _cache.clear()
        _principal = None
        _principal_failed_at = None
        return
    for key in [k for k in _cache if k[0] == tenant_id]:
        del _cache[key]


def _default_sts(region: Optional[str]):
    # A fresh Session per call: the module-level default session is not thread-safe
    # and this runs inside asyncio.to_thread.
    return boto3.session.Session().client("sts", region_name=region, config=_STS_CONFIG)


def _error_code(e: Exception) -> str:
    if isinstance(e, ClientError):
        return e.response.get("Error", {}).get("Code", "ClientError")
    return type(e).__name__


async def bedrock_credentials(cfg: ProviderConfig, *, now: Optional[datetime] = None,
                              sts_factory: Optional[Callable] = None) -> Optional[dict]:
    if cfg.source != "tenant":
        return None  # deployment: boto3 standard credential chain
    mode = cfg.auth_mode
    if mode not in ("role", "keys"):
        raise AIUnavailable("tenant Bedrock auth mode must be role or keys")
    if mode == "keys":
        s = cfg.secrets or {}
        if not s.get("access_key_id") or not s.get("secret_access_key"):
            raise AIUnavailable("Bedrock access keys not configured")
        creds = {"aws_access_key_id": s.get("access_key_id"), "aws_secret_access_key": s.get("secret_access_key")}
        if s.get("session_token"):
            creds["aws_session_token"] = s["session_token"]
        return creds
    if not cfg.role_arn:
        raise AIUnavailable("Bedrock role ARN not configured")

    now = now or datetime.now(timezone.utc)
    key = (cfg.tenant_id or 0, cfg.role_arn or "", cfg.external_id or "")
    hit = _cache.get(key)
    if hit and hit[1] - _REFRESH_MARGIN > now:
        return dict(hit[0])

    factory = sts_factory or _default_sts

    def _assume():
        client = factory(cfg.region)
        kwargs = {"RoleArn": cfg.role_arn, "RoleSessionName": f"sochub-tenant-{cfg.tenant_id}",
                  "DurationSeconds": _DURATION_SECONDS}
        if cfg.external_id:
            kwargs["ExternalId"] = cfg.external_id
        return client.assume_role(**kwargs)["Credentials"]

    try:
        c = await asyncio.to_thread(_assume)
    except Exception as e:
        code = _error_code(e)
        # Only the error code, role ARN and tenant id — never keys, tokens or the external id.
        logger.warning("Bedrock sts:AssumeRole failed: code=%s role_arn=%s tenant_id=%s",
                       code, cfg.role_arn, cfg.tenant_id)
        raise AIUnavailable(f"Bedrock role assumption failed: {code}")
    creds = {"aws_access_key_id": c["AccessKeyId"], "aws_secret_access_key": c["SecretAccessKey"],
             "aws_session_token": c["SessionToken"]}
    _cache[key] = (creds, c["Expiration"])
    return dict(creds)


async def deployment_principal_arn(*, now: Optional[datetime] = None) -> Optional[str]:
    """Best-effort ARN of the server's own AWS identity, for the trust-policy snippet.

    Successes are cached for the process lifetime; failures for 10 minutes, so a
    host without AWS credentials doesn't hit STS/IMDS on every request.
    """
    global _principal, _principal_failed_at
    if _principal:
        return _principal
    now = now or datetime.now(timezone.utc)
    if _principal_failed_at and now - _principal_failed_at < _NEGATIVE_TTL:
        return None
    try:
        ident = await asyncio.to_thread(lambda: _default_sts(None).get_caller_identity())
    except Exception as e:
        logger.warning("sts:GetCallerIdentity failed: code=%s", _error_code(e))
        _principal_failed_at = now
        return None
    _principal = ident.get("Arn")
    if not _principal:
        _principal_failed_at = now
    return _principal
