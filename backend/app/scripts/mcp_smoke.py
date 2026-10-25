"""End-to-end smoke check of the MCP server over real HTTP (through nginx when --base-url points at it).

    python -m app.scripts.mcp_smoke --base-url http://localhost --allow-write

It WRITES to the configured database (a throw-away tenant, user, OAuth client, alert and their audit rows, all
removed again by explicit id), so it refuses to run without ``--allow-write`` and when
``ENVIRONMENT == "production"``.

Steps: discovery (PRM named by the 401 header -> authorization server metadata) -> dynamic client registration ->
MCP initialize -> tools/list -> whoami -> list_alerts -> cleanup.

SHORTCUT (be aware): the interactive browser sign-in and consent screen cannot be driven from a script. Instead of
the authorization-code exchange, this script creates a throw-away tenant (mcp_enabled), user, membership and alert
directly in the database, creates an OAuth session for the client it just registered through DCR and mints the
access token with ``issue_oauth_access_token``. Everything after that (token verification, transport security,
nginx, tools, audit) is the real path. Everything created is deleted BY EXPLICIT ID at the end.

The request ``Host`` header is forced to the PUBLIC_BASE_URL host so the transport-security allowlist accepts
requests sent to an internal address such as ``http://frontend:8080``.
"""
import argparse
import asyncio
import json
import re
import secrets
import sys
from urllib.parse import urlparse

import httpx
from sqlalchemy import delete, func, select

from app.auth import sessions
from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.mcp.tools import MIN_ROLE
from app.models.audit_log import AuditLog
from app.models.auth_session import AuthSession
from app.models.case import Alert
from app.models.membership import TenantMembership
from app.models.oauth_client import OAuthClient
from app.models.tenant import Tenant
from app.models.user import User
from app.oauth.resources import RESOURCES
from app.oauth.tokens import issue_oauth_access_token

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
failures = 0


def report(ok: bool, step: str, detail: str = "") -> bool:
    global failures
    if not ok:
        failures += 1
    print(f"{'PASS' if ok else 'FAIL'}  {step}" + (f"  ({detail})" if detail else ""))
    return ok


def parse(r: httpx.Response) -> dict:
    if r.headers.get("content-type", "").startswith("text/event-stream"):
        msgs = [json.loads(ln[5:]) for ln in r.text.splitlines() if ln.startswith("data:") and ln[5:].strip()]
        return [m for m in msgs if "id" in m][-1]
    return r.json()


async def run(base: str) -> None:
    base = base.rstrip("/")
    public_host = urlparse(settings.PUBLIC_BASE_URL).netloc
    h = secrets.token_hex(3)
    created = {"tenant": None, "user": None, "client": None, "alert": None}
    async with AsyncSessionLocal() as db:  # audit rows of this run are the throw-away tenant's rows above this id
        audit_floor = (await db.execute(select(func.max(AuditLog.id)))).scalar() or 0
    async with httpx.AsyncClient(base_url=base, headers={"Host": public_host}, timeout=30) as c:
        try:
            # 1. discovery
            r = await c.post("/mcp", json={}, headers=ACCEPT)
            m = re.search(r'resource_metadata="([^"]+)"', r.headers.get("www-authenticate", ""))
            if not report(r.status_code == 401 and m is not None, "unauthenticated /mcp -> 401 with resource_metadata",
                          f"status {r.status_code}"):
                return
            prm_path = urlparse(m.group(1)).path  # the advertised origin is PUBLIC_BASE_URL; reach it via --base-url
            prm = (await c.get(prm_path)).json()
            if not report(prm.get("resource") == RESOURCES["mcp"].url and bool(prm.get("authorization_servers")),
                          "protected resource metadata", prm_path):
                return
            as_meta = (await c.get(urlparse(prm["authorization_servers"][0]).path.rstrip("/")
                                   + "/.well-known/oauth-authorization-server")).json()
            if not report(all(k in as_meta for k in ("authorization_endpoint", "token_endpoint",
                                                     "registration_endpoint")),
                          "authorization server metadata", as_meta.get("issuer", "")):
                return

            # 2. dynamic client registration
            reg = await c.post(urlparse(as_meta["registration_endpoint"]).path, json={
                "client_name": f"mcp-smoke-{h}", "redirect_uris": ["http://127.0.0.1:9/cb"],
                "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"]})
            if not report(reg.status_code == 201 and "client_id" in reg.json(), "dynamic client registration",
                          f"status {reg.status_code}"):
                return
            created["client"] = reg.json()["client_id"]

            # 3. shortcut for the consent + code exchange: rows created directly, token minted locally
            async with AsyncSessionLocal() as db:
                t = Tenant(name="mcp-smoke", slug=f"mcp-smoke-{h}", mcp_enabled=True)
                u = User(email=f"mcp-smoke-{h}@example.test", hashed_password=get_password_hash(secrets.token_hex(8)),
                         is_active=True)
                db.add_all([t, u])
                await db.flush()
                created["tenant"], created["user"] = int(t.id), int(u.id)
                db.add(TenantMembership(user_id=u.id, tenant_id=t.id, role="analyst"))
                a = Alert(source="smoke", external_id=f"smoke-{h}", title="MCP smoke alert", payload={"k": "v"},
                          tenant_id=t.id)
                db.add(a)
                await db.flush()
                created["alert"] = int(a.id)
                row, _ = await sessions.create_session(db, u, kind="oauth", amr="pwd", tenant_id=t.id,
                                                       client_id=created["client"], resource=RESOURCES["mcp"].url,
                                                       scope="mcp")
                await db.commit()
                token = issue_oauth_access_token(row, u)
            report(True, "test tenant/user/alert + OAuth session created (consent/code step SHORTCUT)")

            hdr = {**ACCEPT, "Authorization": f"Bearer {token}"}
            n = 0

            async def post(payload, extra=None):
                return await c.post("/mcp", json=payload, headers={**hdr, **(extra or {})})

            # 4. initialize
            n += 1
            r = await post({"jsonrpc": "2.0", "id": n, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": f"mcp-smoke-{h}", "version": "1"}}})
            if not report(r.status_code == 200, "MCP initialize", f"status {r.status_code}"):
                print(r.text[:300])
                return
            report("mcp-session-id" not in r.headers, "stateless transport (no Mcp-Session-Id)")
            sess = {"Mcp-Protocol-Version": "2025-11-25"}
            r = await post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sess)
            report(r.status_code == 202, "notifications/initialized", f"status {r.status_code}")

            # 5. tools/list
            n += 1
            r = await post({"jsonrpc": "2.0", "id": n, "method": "tools/list", "params": {}}, sess)
            names = {t["name"] for t in parse(r)["result"]["tools"]} if r.status_code == 200 else set()
            report(names == set(MIN_ROLE), "tools/list", f"{len(names)} tools, expected {len(MIN_ROLE)}")

            async def call(tool, **args):
                nonlocal n
                n += 1
                r = await post({"jsonrpc": "2.0", "id": n, "method": "tools/call",
                                "params": {"name": tool, "arguments": args}}, sess)
                res = parse(r)["result"]
                return res.get("isError"), (res["content"][0]["text"] if res.get("content") else "")

            # 6. whoami
            err, text = await call("whoami")
            report(not err and "mcp-smoke" in text and "analyst" in text, "whoami", text[:120].replace("\n", " "))

            # 7. list_alerts
            err, text = await call("list_alerts")
            report(not err and "MCP smoke alert" in text, "list_alerts sees the seeded alert",
                   f"{len(text)} chars")
        except Exception as e:  # noqa: BLE001 - a smoke check reports, never crashes silently
            report(False, "unexpected error", f"{type(e).__name__}: {e}")
        finally:
            # 8. cleanup by explicit ids only
            try:
                async with AsyncSessionLocal() as db:
                    uid, tid = created["user"], created["tenant"]
                    if created["alert"]:
                        await db.execute(delete(Alert).where(Alert.id == created["alert"]))
                    if uid:
                        await db.execute(delete(AuthSession).where(AuthSession.user_id == uid))
                        await db.execute(delete(TenantMembership).where(TenantMembership.user_id == uid))
                    if created["client"]:
                        await db.execute(delete(AuthSession).where(AuthSession.client_id == created["client"]))
                        await db.execute(delete(OAuthClient).where(OAuthClient.id == created["client"]))
                    if tid:
                        audit_ids = (await db.execute(select(AuditLog.id).where(
                            AuditLog.tenant_id == tid, AuditLog.id > audit_floor))).scalars().all()
                        created["audit_rows"] = len(audit_ids)
                        if audit_ids:
                            await db.execute(delete(AuditLog).where(AuditLog.id.in_(audit_ids)))
                    if uid:
                        await db.execute(delete(User).where(User.id == uid))
                    if tid:
                        await db.execute(delete(Tenant).where(Tenant.id == tid))
                    await db.commit()
                report(True, "cleanup (by explicit ids)", json.dumps(created))
            except Exception as e:  # noqa: BLE001
                report(False, "cleanup", f"{type(e).__name__}: {e} -- leftover ids: {json.dumps(created)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="SOC Hub MCP end-to-end smoke check")
    ap.add_argument("--base-url", default="http://localhost", help="where the stack is reachable from here")
    ap.add_argument("--allow-write", action="store_true",
                    help="required: the check creates (and then deletes) rows in the configured database")
    args = ap.parse_args()
    if not args.allow_write:
        print("Refusing to run: this writes to the database. Pass --allow-write on a non-production stack.")
        return 2
    if settings.ENVIRONMENT == "production":
        print("Refusing to run: ENVIRONMENT is production.")
        return 2

    async def go():
        await run(args.base_url)
        await engine.dispose()

    asyncio.run(go())
    print("RESULT:", "FAIL" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
