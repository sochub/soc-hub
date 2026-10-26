import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from secrets import token_urlsafe
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from sqlalchemy import select

from app.models.tenant import Tenant
from app.models.tenant_secret import TenantSecret
from app.secrets.hosts import host_allowed
from app.secrets.refs import placeholder_re
from app.secrets.resolve import redact, redact_text, resolve_for_request
from app.workflows.nodes import NodeContext, NodeError, RetryableNodeError, executor
from app.workflows.ssrf import SSRFError, assert_url_allowed

MAX_BODY = 1_000_000
AUTH_TYPES = ("oauth2_client_credentials", "oauth2_private_key_jwt", "basic", "aws_sigv4", "google_service_account")
TOKEN_AUTH_TYPES = ("oauth2_client_credentials", "oauth2_private_key_jwt", "google_service_account")
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_BAD_KEY = "auth.private_key is not a valid PEM or JWK private key"
_NOT_DPOP = "token is not DPoP-bound — enable Require DPoP on the app or set dpop: false"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # fixed: the key JSON's token_uri is ignored
_AWS_NAME = re.compile(r"[a-z0-9-]{2,32}")
# ponytail: per-process cache; move to Redis if workers multiply.
# sha256(tenant, token_url, form before signing, dpop) -> (access_token, expires_at monotonic, (DPoP key, ath) or None)
_TOKENS: dict = {}
logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO ("HTTP Request: GET https://...?key=<secret>"), botocore.auth logs the
# SigV4 signature at DEBUG: never let that through.
for _name in ("httpx", "httpcore", "botocore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def _strings(o):
    """All strings in o, including dict keys."""
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _strings(v)


def _log_secret_use(tenant_id, names, host, outcome):
    # names/ids/hosts only, never values
    logger.info("secret use tenant=%s names=%s host=%s outcome=%s", tenant_id, sorted(names), host, outcome)  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure -- names/hosts only, never values


@executor("http_request")
async def run_http(nctx: NodeContext, config: dict) -> dict:
    tenant = (await nctx.db.execute(select(Tenant).where(Tenant.id == nctx.run.tenant_id))).scalars().first()
    url = str(config["url"])
    try:
        # getaddrinfo blocks, so keep the guard off the event loop
        pinned_ip = await asyncio.to_thread(assert_url_allowed, url, tenant.workflow_http_allowlist or [])
    except SSRFError as e:
        raise NodeError(str(e))

    method = str(config.get("method", "GET")).upper()
    dpop_key = None
    headers = {str(k): str(v) for k, v in (config.get("headers") or {}).items()}
    auth = config.get("auth")
    if auth not in (None, ""):
        if not isinstance(auth, dict) or auth.get("type") not in AUTH_TYPES:
            raise NodeError("auth.type must be oauth2_client_credentials, oauth2_private_key_jwt, basic, aws_sigv4 or google_service_account")
        headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}  # auth wins
    body = config.get("body")

    # Secrets are substituted only now, after the SSRF check (a secret can never be in the host).
    used: dict = {}
    host = ""
    nonce = nctx.secret_nonce
    issued = set(nctx.secret_names or ())
    rx = placeholder_re(nonce, issued) if nonce else None
    if rx and any(rx.search(x) for x in [url, *headers.keys(), *headers.values(), *_strings(body)]):
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            raise NodeError("invalid URL") from None
        try:
            url, headers, body, used = await resolve_for_request(
                nctx.db, nctx.run.tenant_id, url, headers, body, host, nonce, issued,
                tenant.workflow_http_allowlist or [])
        except NodeError:
            names = {n for x in [url, *headers.keys(), *headers.values(), *_strings(body)] for n in rx.findall(x)}
            _log_secret_use(nctx.run.tenant_id, names, host, "blocked")
            raise
    hidden = used  # values masked out of this request's output and errors
    if auth not in (None, ""):
        try:
            api_host = urlsplit(url).hostname or ""
        except ValueError:
            raise NodeError("invalid URL") from None
        if auth["type"] in TOKEN_AUTH_TYPES:
            token, dpop, token_key = await _oauth_token(nctx, tenant, auth, api_host)
            dpop_key, ath = dpop or (None, None)
            if dpop_key is not None:
                headers = {k: v for k, v in headers.items() if k.lower() != "dpop"}  # auth wins
                headers["Authorization"] = f"DPoP {token}"
            else:
                headers["Authorization"] = f"Bearer {token}"
            hidden = {**used, "": token}  # "" never reaches _log_secret_use (that logs `used` names only)
        else:
            # Basic/SigV4 credentials go to the request host itself, so normal resolution enforces binding.
            host = api_host
            creds, auth_used = await _auth_creds(nctx, tenant, auth, url, api_host)
            used = {**used, **auth_used}
            if auth["type"] == "basic":
                derived = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
                headers["Authorization"] = f"Basic {derived}"
            else:
                try:
                    headers, body, derived = _sigv4(url, method, headers, body, creds)
                except NodeError:
                    raise
                except Exception as e:  # never echo library text: it may quote the credentials
                    raise NodeError(f"auth signing failed: {type(e).__name__}") from None
            hidden = {**used, "": derived}  # "" never reaches _log_secret_use
    outcome = "error"  # also for CancelledError/BaseException: only a completed request is "ok"
    try:
        if dpop_key is None:
            resp = await _send(url, method, headers, body, pinned_ip, bool(hidden))
        else:
            # One proof per request. hidden is a fresh dict here (it holds the token), never `used`.
            hidden["dpop:0"] = headers["DPoP"] = _dpop_proof(dpop_key, method, url, ath=ath)
            resp = await _send(url, method, headers, body, pinned_ip, True)
            nonce_hdr = _header(resp, "dpop-nonce")
            if resp["status"] == 401 and "use_dpop_nonce" in _header(resp, "www-authenticate") and nonce_hdr:
                hidden["dpop:1"] = headers["DPoP"] = _dpop_proof(dpop_key, method, url, nonce=nonce_hdr, ath=ath)
                resp = await _send(url, method, headers, body, pinned_ip, True)  # exactly one retry
            if resp["status"] == 401 and "invalid_token" in _header(resp, "www-authenticate"):
                _TOKENS.pop(token_key, None)  # the next run mints a fresh token
        out = redact(resp, hidden)
        outcome = "ok"
        return out
    except NodeError as e:
        raise type(e)(redact_text(str(e), hidden)) from None
    except Exception as e:  # unexpected library error after substitution: never echo its text
        if not hidden:
            raise
        raise NodeError(f"request failed: {type(e).__name__}") from None
    finally:
        if used:
            _log_secret_use(nctx.run.tenant_id, used, host, outcome)


async def _oauth_token(nctx: NodeContext, tenant, auth: dict, api_host: str) -> tuple[str, tuple | None, str]:
    """Client-credentials grant -> (token, (DPoP key, ath) or None, cache key). Same SSRF guard and secret host
    binding as the main request, checked against the token host. The minted token is a derived credential,
    so it is bound to the same hosts as the client secret: `api_host` must be allowed by every secret used
    here. The token and DPoP key are returned to the caller only; never logged or put in output."""
    google = auth["type"] == "google_service_account"
    pkjwt = auth["type"] == "oauth2_private_key_jwt"
    dpop = pkjwt and auth.get("dpop") is True  # a real bool only: "false" must not turn it on
    if google:  # JWT-bearer grant, signed below once the key is resolved
        token_url, secret_field, auth_field = GOOGLE_TOKEN_URL, "key", "service_account_key"
        form = {"key": str(auth.get("service_account_key") or ""), "subject": str(auth.get("subject") or ""),
                "scope": str(auth.get("scopes") or "")}
        if not form["key"] or not form["scope"]:
            raise NodeError("auth needs service_account_key and scopes")
    elif pkjwt:  # client assertion (RFC 7523), signed below once the key is resolved
        token_url, secret_field = str(auth.get("token_url") or ""), "private_key"
        auth_field = secret_field
        form = {k: str(auth.get(k) or "") for k in ("client_id", "private_key", "scope", "key_id")}
        if not token_url or not form["client_id"] or not form["private_key"] or not form["scope"]:
            raise NodeError("auth needs token_url, client_id, private_key and scope")
    else:
        token_url, secret_field = str(auth.get("token_url") or ""), "client_secret"
        auth_field = secret_field
        form = {"grant_type": "client_credentials", "client_id": str(auth.get("client_id") or ""),
                "client_secret": str(auth.get("client_secret") or "")}
        for opt in ("scope", "audience"):  # audience: e.g. Wiz requires audience=wiz-api
            if auth.get(opt):
                form[opt] = str(auth[opt])
        if not token_url or not form["client_id"] or not form["client_secret"]:
            raise NodeError("auth needs token_url, client_id and client_secret")
    allowlist = tenant.workflow_http_allowlist or []
    try:
        pinned_ip = await asyncio.to_thread(assert_url_allowed, token_url, allowlist)
    except SSRFError as e:
        raise NodeError(f"token URL: {e}")
    try:
        host = urlsplit(token_url).hostname or ""
    except ValueError:
        raise NodeError("invalid token URL") from None
    nonce, issued = nctx.secret_nonce, set(nctx.secret_names or ())
    try:
        token_url, _, form, used = await resolve_for_request(
            nctx.db, nctx.run.tenant_id, token_url, {}, form, host, nonce, issued, allowlist)
    except NodeError:
        rx = placeholder_re(nonce, issued) if nonce else None
        names = {n for x in [token_url, *form.values()] for n in rx.findall(x)} if rx else set()
        _log_secret_use(nctx.run.tenant_id, names, host, "blocked")
        raise
    # The client secret must come from the secret store, and its host binding also covers the API host.
    if form[secret_field] not in used.values():
        raise NodeError(f"auth.{auth_field} must be a secret reference like {{{{ secrets.NAME }}}}")
    rows = (await nctx.db.execute(select(TenantSecret).where(
        TenantSecret.tenant_id == nctx.run.tenant_id, TenantSecret.name.in_(list(used))))).scalars().all()
    for row in rows:
        if not host_allowed(api_host, row.allowed_hosts):
            _log_secret_use(nctx.run.tenant_id, used, api_host, "blocked")
            raise NodeError(f"Secret {row.name} is not allowed for host {api_host} "
                            "(an OAuth token is bound to the same hosts as its client secret)")
    # Keyed before signing: a signed form is unique per call (iat/jti). scope is in the form; dpop changes the
    # token kind, so it is part of the key too.
    key = hashlib.sha256(json.dumps([nctx.run.tenant_id, token_url, form, dpop],
                                    sort_keys=True).encode()).hexdigest()
    cached = _TOKENS.get(key)
    if cached and cached[1] > time.monotonic():
        return cached[0], cached[2], key
    if google:
        form = _google_assertion(form)
        used = {**used, "": form["assertion"]}  # redaction only; "" is dropped before _log_secret_use
    elif pkjwt:
        client_id, private_key, key_id = form["client_id"], form["private_key"], form["key_id"] or None
        assertion = _client_assertion(client_id, token_url, private_key, key_id)
        form = {"grant_type": "client_credentials", "scope": form["scope"],
                "client_assertion_type": CLIENT_ASSERTION_TYPE, "client_assertion": assertion}
        used = {**used, "": assertion}  # redaction only; "" is dropped before _log_secret_use

    # Redaction only (assertion and proofs); `used` names are what _log_secret_use logs.
    hide = dict(used)
    dpop_key = ec.generate_private_key(ec.SECP256R1()) if dpop else None  # in memory, cached with the token

    async def post(nonce=None):
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        if dpop_key is not None:
            hide[f"dpop:{len(hide)}"] = headers["DPoP"] = _dpop_proof(dpop_key, "POST", token_url, nonce=nonce)
        return await _send(token_url, "POST", headers, urlencode(form), pinned_ip, True)

    outcome = "error"
    try:
        resp = await post()
        nonce_hdr = _header(resp, "dpop-nonce")
        if (dpop_key is not None and resp["status"] == 400 and isinstance(resp["body"], dict)
                and resp["body"].get("error") == "use_dpop_nonce" and nonce_hdr):
            if pkjwt:  # a fresh assertion: some servers reject a replayed jti
                form = {**form, "client_assertion": _client_assertion(
                    client_id, token_url, private_key, key_id)}
                hide[f"assertion:{len(hide)}"] = form["client_assertion"]
            resp = await post(nonce_hdr)  # exactly one retry
        outcome = "ok"
    except NodeError as e:
        raise type(e)(f"token request failed: {redact_text(str(e), hide)}") from None
    except Exception as e:
        raise NodeError(f"token request failed: {type(e).__name__}") from None
    finally:
        names = {k: v for k, v in used.items() if k}
        if names:
            _log_secret_use(nctx.run.tenant_id, names, host, outcome)
    token = resp["body"].get("access_token") if isinstance(resp["body"], dict) else None
    if not 200 <= resp["status"] < 300 or not isinstance(token, str) or not token:  # CrowdStrike answers 201
        raise NodeError(f"token request failed: HTTP {resp['status']}{_oauth_error(resp, hide)}")
    if any(c in token for c in "\r\n") or not token.isascii():
        raise NodeError("token request failed: unusable access_token")
    if dpop_key is not None and str(resp["body"].get("token_type") or "").lower() != "dpop":
        raise NodeError(_NOT_DPOP)
    try:
        ttl = int(resp["body"].get("expires_in") or 0) - 60
    except (TypeError, ValueError):
        ttl = 0
    # ath (RFC 9449 4.2) is fixed per token, so it is derived once here and cached with the DPoP key.
    dpop_binding = (dpop_key, _b64url(hashlib.sha256(token.encode()).digest())) if dpop_key is not None else None
    if ttl > 0:
        _TOKENS[key] = (token, time.monotonic() + ttl, dpop_binding)
    return token, dpop_binding, key


def _oauth_error(resp: dict, hide: dict) -> str:
    """" (code: description)" from the RFC 6749 error fields only (no other body field), single-line, capped and
    redacted: it is what tells the admin why the token was refused (e.g. Okta's invalid_client reasons)."""
    body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
    parts = [body.get(k) for k in ("error", "error_description")]
    parts = [" ".join(p.split()) for p in parts if isinstance(p, str) and p.strip()]
    return f" ({redact_text(': '.join(parts), hide)[:200]})" if parts else ""  # redact before cutting


def _header(resp: dict, name: str) -> str:
    """A response header value, matched case-insensitively ("" when absent)."""
    for k, v in (resp.get("headers") or {}).items():
        if k.lower() == name:
            return str(v)
    return ""


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _htu(url: str) -> str:
    """DPoP htu: scheme://host[:port]/path, without userinfo, query or fragment."""
    parts = urlsplit(url)
    scheme, host = parts.scheme.lower(), parts.netloc.rpartition('@')[2].lower()
    default = {"https": ":443", "http": ":80"}.get(scheme)
    if default and host.endswith(default):
        host = host[: -len(default)]
    return f"{scheme}://{host}{parts.path or '/'}"


def _dpop_proof(key, method: str, url: str, *, nonce: str | None = None, ath: str | None = None) -> str:
    """ES256 DPoP proof (RFC 9449 section 4). The header jwk carries the public key only."""
    nums = key.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "x": _b64url(nums.x.to_bytes(32, "big")),
           "y": _b64url(nums.y.to_bytes(32, "big"))}
    claims = {"htm": method.upper(), "htu": _htu(url), "iat": int(time.time()), "jti": token_urlsafe(24)}
    if nonce:
        claims["nonce"] = nonce
    if ath:
        claims["ath"] = ath
    try:
        return jwt.encode(claims, key, algorithm="ES256", headers={"typ": "dpop+jwt", "jwk": jwk})
    except Exception:  # never echo library text
        raise NodeError("DPoP proof signing failed") from None


def _google_assertion(form: dict) -> dict:
    """Signed RS256 JWT for the JWT-bearer grant. A malformed key fails generically: never echo it."""
    try:
        k = json.loads(form["key"])
        now = int(time.time())
        claims = {"iss": k["client_email"], "scope": form["scope"], "aud": GOOGLE_TOKEN_URL, "iat": now, "exp": now + 3600}
        if form["subject"]:
            claims["sub"] = form["subject"]
        assertion = jwt.encode(claims, k["private_key"], algorithm="RS256", headers={"kid": k["private_key_id"]})
    except Exception:
        raise NodeError("auth.service_account_key is not a valid service account JSON key") from None
    return {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}


def _load_private_key(raw: str) -> tuple[object, str, str | None]:
    """(key, alg, jwk kid) from a private JWK (JSON) or a PEM (PKCS#8 / PKCS#1 / SEC1). RSA -> RS256,
    EC P-256 -> ES256. Errors are fixed strings: never echo key text or library error text."""
    kid = None
    try:
        try:
            jwk = json.loads(raw)
        except ValueError:
            jwk = None
        if isinstance(jwk, dict):
            if not jwk.get("d"):  # a public JWK cannot sign
                raise ValueError
            key = jwt.PyJWK(jwk).key
            kid = jwk.get("kid") if isinstance(jwk.get("kid"), str) and jwk.get("kid") else None
        else:
            key = load_pem_private_key(raw.encode(), password=None)
    except Exception:
        raise NodeError(_BAD_KEY) from None
    if isinstance(key, rsa.RSAPrivateKey):
        return key, "RS256", kid
    if isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1):
        return key, "ES256", kid
    raise NodeError("auth.private_key: unsupported key type")


def _client_assertion(client_id: str, token_url: str, raw_key: str, key_id: str | None) -> str:
    """Signed client assertion (RFC 7523 section 2.2) for private_key_jwt client authentication."""
    key, alg, jwk_kid = _load_private_key(raw_key)
    now = int(time.time())
    claims = {"iss": client_id, "sub": client_id, "aud": token_url, "iat": now, "exp": now + 300,
              "jti": token_urlsafe(24)}
    kid = key_id or jwk_kid
    try:
        return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid} if kid else None)
    except Exception:
        raise NodeError(_BAD_KEY) from None


async def _auth_creds(nctx: NodeContext, tenant, auth: dict, url: str, host: str) -> tuple[dict, dict]:
    """Resolve basic/aws_sigv4 credentials against the request host (same binding as headers)."""
    keys, secret_key = {"basic": (("username", "password"), "password"),
                        "aws_sigv4": (("access_key_id", "secret_access_key", "session_token"), "secret_access_key")
                        }[auth["type"]]
    creds = {k: str(auth.get(k) or "") for k in keys}
    nonce, issued = nctx.secret_nonce, set(nctx.secret_names or ())
    try:
        _, _, creds, used = await resolve_for_request(
            nctx.db, nctx.run.tenant_id, url, {}, creds, host, nonce, issued, tenant.workflow_http_allowlist or [])
    except NodeError:
        rx = placeholder_re(nonce, issued) if nonce else None
        names = {n for x in creds.values() for n in rx.findall(x)} if rx else set()
        _log_secret_use(nctx.run.tenant_id, names, host, "blocked")
        raise
    if not creds[secret_key] or creds[secret_key] not in used.values():
        raise NodeError(f"auth.{secret_key} must be a secret reference like {{{{ secrets.NAME }}}}")
    if auth["type"] == "aws_sigv4":
        if not creds["access_key_id"]:
            raise NodeError("auth needs access_key_id")
        for k in ("region", "service"):
            creds[k] = str(auth.get(k) or "")
            if not _AWS_NAME.fullmatch(creds[k]):
                raise NodeError(f"auth.{k} must match [a-z0-9-]{{2,32}}")
    return creds, used


def _sigv4(url, method, headers, body, creds) -> tuple[dict, str, str]:
    """Sign the exact bytes and Host that _send will send. Returns (headers, body str, signature).
    ponytail: static keys from tenant secrets only; no IAM role / instance-profile / STS assume-role."""
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
        if not any(k.lower() == "content-type" for k in headers):
            headers = {**headers, "Content-Type": "application/json"}
    body = "" if body in (None, "") else str(body)
    try:
        netloc = httpx.URL(url).netloc.decode("ascii")  # same Host value _send sets when pinning
    except httpx.InvalidURL:
        raise NodeError("invalid URL") from None
    headers = {k: v for k, v in headers.items() if k.lower() not in ("host", "x-amz-date", "x-amz-security-token")}
    req = AWSRequest(method=method, url=url, data=body.encode(), headers={**headers, "Host": netloc})
    SigV4Auth(Credentials(creds["access_key_id"], creds["secret_access_key"], creds["session_token"] or None),
              creds["service"], creds["region"]).add_auth(req)
    signature = req.headers["Authorization"].rsplit("Signature=", 1)[1]
    return dict(req.headers.items()), body, signature


async def _send(url, method, headers, body, pinned_ip, secret=False) -> dict:
    # target / Host / SNI are all built from the URL after substitution
    target, extensions = url, {}
    try:
        parsed = httpx.URL(url)
        if pinned_ip:
            # Connect to the vetted IP (no second DNS lookup to rebind), but keep the hostname for the
            # Host header and for TLS: httpcore uses `sni_hostname` as server_hostname, so SNI and
            # certificate verification are still against the original name.
            target = parsed.copy_with(host=pinned_ip)
    except httpx.InvalidURL as e:
        raise NodeError("invalid URL" if secret else f"invalid URL: {e}")
    if pinned_ip:
        headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        headers["Host"] = parsed.netloc.decode("ascii")
        extensions = {"sni_hostname": parsed.host}
    kwargs = {"json": body} if isinstance(body, (dict, list)) else ({"content": str(body)} if body not in (None, "") else {})

    async def _fetch():
        buf = bytearray()
        truncated = False
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            async with client.stream(method, target, headers=headers, extensions=extensions, **kwargs) as resp:
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > MAX_BODY:
                        truncated = True
                        break
                return resp.status_code, dict(resp.headers), bytes(buf[:MAX_BODY]), truncated

    try:
        status, resp_headers, raw, truncated = await asyncio.wait_for(_fetch(), timeout=15)
    except asyncio.TimeoutError:
        raise RetryableNodeError(f"request to {'<redacted url>' if secret else url} timed out after 15s")
    except httpx.InvalidURL as e:
        raise NodeError("invalid URL" if secret else f"invalid URL: {e}")
    except httpx.TransportError as e:
        raise RetryableNodeError(f"request failed: {type(e).__name__ if secret else e}")
    except httpx.HTTPError as e:
        raise NodeError(f"request failed: {type(e).__name__ if secret else e}")
    if status >= 500:
        raise RetryableNodeError(f"HTTP {status} from {'<redacted url>' if secret else url}")

    try:
        parsed_body = json.loads(raw)
    except ValueError:
        parsed_body = raw.decode(errors="replace")
    # 4xx is returned (not raised) so workflows can branch on output.status.
    out = {"status": status, "headers": resp_headers, "body": parsed_body}
    if truncated:
        out["truncated"] = True
    return out
