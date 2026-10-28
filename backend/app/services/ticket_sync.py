"""Two-way case <-> ticket sync with a tenant's Jira, HaloPSA or ConnectWise Manage.

Tickets belong to case **tasks** (one ticket per task), not to whole cases. One periodic job (``sync_tenant``)
does everything by polling, so no task code path needs a hook:
- creates tickets for new open tasks of cases at/above ``auto_create_min_severity``;
- pulls each ticket's status (closing the ticket ticks its task) and new comments (into the case activity);
- posts a ticked task's result on its ticket (and a note when it's reopened).

Every request goes to the connection's base URL through the SSRF guard, pinned to the vetted IP, https only.
Provider payloads follow each vendor's public API docs; they have not been run against live accounts.
"""
import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.case import Case, CaseLink, CaseStatus, TimelineEvent
from app.models.case_task import CaseTask
from app.models.tenant import Tenant
from app.models.ticket_connection import TicketConnection
from app.utils.audit import create_audit_log
from app.utils.crypto import decrypt, encrypt
from app.workflows.ssrf import HTTPS_ONLY, SSRFError, assert_url_allowed

logger = logging.getLogger(__name__)

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
MAX_TEXT = 30_000
MAX_NEW_COMMENTS = 50  # per link per sync
MAX_AUTO_CREATE = 20  # per tenant per sync
_MENTION = re.compile(r"@\[([^\]]+)\]\(user:\d+\)")


class TicketError(Exception):
    """Shown to admins as the connection's last_error and to analysts when a ticket action fails."""


# ── HTTP ─────────────────────────────────────────────────────────


async def _call(conn: TicketConnection, allowlist: list, method: str, path: str, *, headers: dict,
                json_body: Any = None, form: Optional[dict] = None, url: Optional[str] = None) -> Any:
    target_url = url or f"{conn.base_url.rstrip('/')}/{path.lstrip('/')}"
    parsed = httpx.URL(target_url)
    if parsed.scheme != "https":
        raise TicketError(HTTPS_ONLY)
    try:
        ip = await asyncio.to_thread(assert_url_allowed, target_url, allowlist)
    except SSRFError as e:
        raise TicketError(str(e)) from None
    headers = {**headers, "Host": parsed.netloc.decode("ascii"), "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            r = await client.request(method, parsed.copy_with(host=ip), headers=headers, json=json_body, data=form,
                                     extensions={"sni_hostname": parsed.host})
    except httpx.HTTPError as e:
        raise TicketError(f"{conn.provider} request failed: {type(e).__name__}") from None
    if r.status_code >= 400:
        raise TicketError(f"{conn.provider} returned HTTP {r.status_code} for {method} {parsed.path}")
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        raise TicketError(f"{conn.provider} returned a non-JSON response for {parsed.path}") from None


def _plain(text: str) -> str:
    return _MENTION.sub(r"@\1", text or "")[:MAX_TEXT]


def _adf(text: str) -> dict:
    """Jira Cloud's Atlassian Document Format: one paragraph per line."""
    lines = (text or "").split("\n") or [""]
    return {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": ln}] if ln else []} for ln in lines]}


def _adf_text(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        sep = "\n" if node.get("type") in ("doc",) else ""
        return sep.join(_adf_text(c) for c in node.get("content", []))
    return ""


# ── providers ────────────────────────────────────────────────────
# Each returns plain dicts: create -> {external_id, key, url}; status -> {status, closed};
# comments -> [{id, author, text}] (oldest first, newer than `after`); add_comment -> None.


ATLASSIAN_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ATLASSIAN_API = "https://api.atlassian.com"


class Jira:
    """Jira Cloud (REST v3) with a person's email + API token, or with an Atlassian service account's OAuth 2.0
    client credentials (calls go to api.atlassian.com/ex/jira/<cloud id>); or Data Center (REST v2, personal access
    token). Links always point at the site (base_url)."""

    def __init__(self, conn, creds, allowlist):
        self.conn, self.creds, self.allowlist = conn, creds, allowlist
        mode = conn.settings.get("deployment") or "cloud"
        self.dc, self.sa = mode == "datacenter", mode == "service_account"
        self.api = "rest/api/2" if self.dc else "rest/api/3"
        self._token = None

    async def _headers(self):
        if self.sa:
            if self._token is None:
                d = await _call(self.conn, self.allowlist, "POST", "", headers={}, url=ATLASSIAN_TOKEN_URL, form={
                    "grant_type": "client_credentials", "client_id": self.creds.get("client_id", ""),
                    "client_secret": self.creds.get("client_secret", "")})
                self._token = (d or {}).get("access_token")
                if not self._token:
                    raise TicketError("Atlassian did not return an access token for the service account")
            return {"Authorization": f"Bearer {self._token}"}
        if self.dc:
            return {"Authorization": f"Bearer {self.creds.get('token', '')}"}
        raw = f"{self.creds.get('email', '')}:{self.creds.get('token', '')}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    async def _req(self, method, path, json_body=None):
        """A Jira REST call: on the site, or through api.atlassian.com for a service account."""
        url = (f"{ATLASSIAN_API}/ex/jira/{self.conn.settings.get('cloud_id', '')}/{self.api}/{path}" if self.sa else None)
        return await _call(self.conn, self.allowlist, method, f"{self.api}/{path}", headers=await self._headers(),
                           json_body=json_body, url=url)

    def _body(self, text):
        return text if self.dc else _adf(text)

    async def check(self):
        await self._req("GET", f"project/{self.conn.settings['project_key']}")

    async def create(self, title, description, severity):
        fields = {"project": {"key": self.conn.settings["project_key"]}, "summary": title[:250],
                  "description": self._body(description),
                  "issuetype": {"name": self.conn.settings.get("issue_type") or "Task"}}
        d = await self._req("POST", "issue", json_body={"fields": fields})
        return {"external_id": str(d["key"]), "key": str(d["key"]),
                "url": f"{self.conn.base_url.rstrip('/')}/browse/{d['key']}"}

    async def status(self, ext):
        d = await self._req("GET", f"issue/{ext}?fields=status")
        st = (d.get("fields") or {}).get("status") or {}
        return {"status": st.get("name"), "closed": (st.get("statusCategory") or {}).get("key") == "done"}

    async def comments(self, ext, after):
        d = await self._req("GET", f"issue/{ext}/comment?orderBy=created&maxResults=100")
        out = []
        for c in (d or {}).get("comments", []):
            if after and int(c["id"]) <= int(after):
                continue
            body = c.get("body")
            out.append({"id": str(c["id"]), "author": (c.get("author") or {}).get("displayName") or "Jira",
                        "text": body if isinstance(body, str) else _adf_text(body)})
        return out

    async def add_comment(self, ext, text):
        d = await self._req("POST", f"issue/{ext}/comment", json_body={"body": self._body(text)})
        return str((d or {}).get("id") or "")


class HaloPSA:
    """HaloPSA: OAuth2 client credentials against <base>/auth/token, API under <base>/api."""

    def __init__(self, conn, creds, allowlist):
        self.conn, self.creds, self.allowlist = conn, creds, allowlist
        self._token = None

    async def _headers(self):
        if self._token is None:
            form = {"grant_type": "client_credentials", "client_id": self.creds.get("client_id", ""),
                    "client_secret": self.creds.get("client_secret", ""), "scope": "all"}
            if self.conn.settings.get("tenant"):
                form["tenant"] = self.conn.settings["tenant"]
            d = await _call(self.conn, self.allowlist, "POST", "auth/token", headers={}, form=form)
            self._token = (d or {}).get("access_token")
            if not self._token:
                raise TicketError("HaloPSA did not return an access token")
        return {"Authorization": f"Bearer {self._token}"}

    async def check(self):
        await _call(self.conn, self.allowlist, "GET", "api/TicketType?count=1", headers=await self._headers())

    async def create(self, title, description, severity):
        s = self.conn.settings
        ticket = {"summary": title[:250], "details": description}
        if s.get("tickettype_id"):
            ticket["tickettype_id"] = int(s["tickettype_id"])
        if s.get("client_id"):
            ticket["client_id"] = int(s["client_id"])
        d = await _call(self.conn, self.allowlist, "POST", "api/Tickets", headers=await self._headers(),
                        json_body=[ticket])
        d = d[0] if isinstance(d, list) else d
        tid = str(d["id"])
        return {"external_id": tid, "key": f"#{tid}",
                "url": f"{self.conn.base_url.rstrip('/')}/ticket?id={tid}"}

    async def status(self, ext):
        d = await _call(self.conn, self.allowlist, "GET", f"api/Tickets/{ext}", headers=await self._headers())
        closed_ids = [int(x) for x in self.conn.settings.get("closed_status_ids") or [9]]
        sid = d.get("status_id")
        return {"status": d.get("status_name") or (f"status {sid}" if sid is not None else None),
                "closed": sid in closed_ids}

    async def comments(self, ext, after):
        d = await _call(self.conn, self.allowlist, "GET", f"api/Actions?ticket_id={ext}&excludesys=true",
                        headers=await self._headers())
        rows = d.get("actions", []) if isinstance(d, dict) else (d or [])
        out = [{"id": str(a["id"]), "author": a.get("who") or "HaloPSA", "text": a.get("note") or ""}
               for a in rows if a.get("note") and (not after or int(a["id"]) > int(after))]
        return sorted(out, key=lambda c: int(c["id"]))

    async def add_comment(self, ext, text):
        d = await _call(self.conn, self.allowlist, "POST", "api/Actions", headers=await self._headers(),
                        json_body=[{"ticket_id": int(ext), "note": text, "outcome": "Note", "hiddenfromuser": True}])
        d = d[0] if isinstance(d, list) and d else d
        return str((d or {}).get("id") or "")


class ConnectWise:
    """ConnectWise Manage (PSA): <base> is the API root, e.g. https://na.myconnectwise.net/v4_6_release/apis/3.0."""

    def __init__(self, conn, creds, allowlist):
        self.conn, self.creds, self.allowlist = conn, creds, allowlist

    def _headers(self):
        raw = f"{self.creds.get('company_id', '')}+{self.creds.get('public_key', '')}:{self.creds.get('private_key', '')}"
        return {"Authorization": "Basic " + base64.b64encode(raw.encode()).decode(),
                "clientId": self.creds.get("client_id", "")}

    async def check(self):
        await _call(self.conn, self.allowlist, "GET", f"service/boards/{int(self.conn.settings['board_id'])}",
                    headers=self._headers())

    async def create(self, title, description, severity):
        s = self.conn.settings
        body = {"summary": title[:100], "initialDescription": description, "board": {"id": int(s["board_id"])},
                "company": {"identifier": s["company_identifier"]}}
        d = await _call(self.conn, self.allowlist, "POST", "service/tickets", headers=self._headers(), json_body=body)
        tid = str(d["id"])
        return {"external_id": tid, "key": f"#{tid}", "url": None}

    async def status(self, ext):
        d = await _call(self.conn, self.allowlist, "GET", f"service/tickets/{int(ext)}", headers=self._headers())
        return {"status": (d.get("status") or {}).get("name"), "closed": bool(d.get("closedFlag"))}

    async def comments(self, ext, after):
        d = await _call(self.conn, self.allowlist, "GET",
                        f"service/tickets/{int(ext)}/notes?orderBy=id%20asc&pageSize=100", headers=self._headers())
        return [{"id": str(n["id"]), "author": n.get("createdBy") or "ConnectWise", "text": n.get("text") or ""}
                for n in (d or []) if n.get("text") and (not after or int(n["id"]) > int(after))]

    async def add_comment(self, ext, text):
        d = await _call(self.conn, self.allowlist, "POST", f"service/tickets/{int(ext)}/notes",
                        headers=self._headers(),
                        json_body={"text": text, "internalAnalysisFlag": True, "detailDescriptionFlag": False})
        return str((d or {}).get("id") or "")


PROVIDERS = {"jira": Jira, "halopsa": HaloPSA, "connectwise": ConnectWise}
PROVIDER_LABEL = {"jira": "Jira", "halopsa": "HaloPSA", "connectwise": "ConnectWise"}


def snapshot(conn: TicketConnection) -> SimpleNamespace:
    """Plain copy of the connection, so a rollback mid-sync (which expires ORM rows) can't break the client."""
    return SimpleNamespace(id=conn.id, tenant_id=conn.tenant_id, provider=conn.provider, base_url=conn.base_url,
                           settings=dict(conn.settings or {}), push_notes=conn.push_notes,
                           auto_create_min_severity=conn.auto_create_min_severity,
                           auto_create_since=conn.auto_create_since)


def client(conn, allowlist: list):
    """`conn` is a snapshot() (or a connection-shaped object) plus its encrypted credentials."""
    return PROVIDERS[conn.provider](conn, json.loads(decrypt(conn.credentials_enc)), allowlist)


def encrypt_credentials(creds: dict) -> str:
    return encrypt(json.dumps(creds))


async def _allowlist(db: AsyncSession, tenant_id: int) -> list:
    return (await db.execute(select(Tenant.workflow_http_allowlist).where(Tenant.id == tenant_id))).scalar() or []


# ── operations ───────────────────────────────────────────────────


def _description(case: Case, task: CaseTask) -> str:
    phase = task.phase.replace("_", " ")
    return (f"{task.description or ''}\n\nIncident response task ({phase}) of SOC Hub case #{case.id}: {case.title}\n"
            f"Severity: {getattr(case.severity, 'value', case.severity)}\n"
            f"{settings.FRONTEND_URL}/cases/{case.id}")[:MAX_TEXT]


async def links_for_case(db: AsyncSession, case_id: int, tenant_id: int) -> dict:
    """{task_id: CaseLink} for the case's synced task tickets."""
    rows = (await db.execute(select(CaseLink).where(
        CaseLink.case_id == case_id, CaseLink.tenant_id == tenant_id, CaseLink.task_id.isnot(None),
        CaseLink.connection_id.isnot(None)))).scalars().all()
    return {lk.task_id: lk for lk in rows}


async def create_ticket(db: AsyncSession, *, conn, case: Case, task: CaseTask, user_id: Optional[int],
                        cl=None) -> CaseLink:
    """Open a ticket for one case task and link it. Raises TicketError; flushes; the caller commits."""
    if task.id in await links_for_case(db, case.id, case.tenant_id):
        raise TicketError("This task already has a ticket")
    cl = cl or client(conn, await _allowlist(db, conn.tenant_id))
    try:
        t = await cl.create(f"[SOC Hub #{case.id}] {task.title}", _description(case, task), case.severity)
    except (KeyError, TypeError, ValueError, IndexError):
        raise TicketError(f"{conn.provider} returned an unexpected response when creating the ticket") from None
    link = CaseLink(case_id=case.id, task_id=task.id, tenant_id=case.tenant_id, connection_id=conn.id,
                    system=conn.provider, external_id=t["external_id"], url=t["url"], pushed_case_status=task.status)
    db.add(link)
    label = PROVIDER_LABEL[conn.provider]
    db.add(TimelineEvent(case_id=case.id, user_id=user_id, event_type="ticket_linked",
                         content=f"{label} ticket {t['key']} created for task **{task.title}**"
                                 + (f": {t['url']}" if t["url"] else "")))
    await create_audit_log(db=db, entity_type="case", entity_id=case.id, action="ticket_created",
                           tenant_id=case.tenant_id, user_id=user_id,
                           changes={"provider": conn.provider, "ticket": t["key"], "task": task.title},
                           actor=None if user_id else "ticket_sync")
    await db.flush()
    return link


async def sync_link(db: AsyncSession, cl, conn, link: CaseLink, case: Case, task: CaseTask) -> None:
    """One task ticket, both ways. Pull: status (a closed ticket ticks the task, with the ticket as the result) and
    comments (into the case activity, naming the task). Push: ticking the task posts its result on the ticket.
    Flushes; the caller commits."""
    from app.services.task_service import update_case_task
    label, key = PROVIDER_LABEL[conn.provider], link.external_id
    st = await cl.status(link.external_id)
    if st["status"] != link.remote_status or st["closed"] != link.remote_closed:
        if link.remote_status is not None or st["closed"]:  # don't announce the first status we see
            db.add(TimelineEvent(case_id=case.id, event_type="ticket_status",
                                 content=f"{label} ticket {key} (task **{task.title}**) is now **{st['status'] or 'unknown'}**"))
        if st["closed"] and not link.remote_closed and task.status != "done":
            task.result, task.result_status = f"Closed in {label} {key} ({st['status'] or 'done'})", "ok"
            task.result_at = datetime.now(timezone.utc)
            await update_case_task(db, case=case, task=task, data={"status": "done"}, user_id=None)
            link.pushed_case_status = "done"  # closed there: nothing to tell the ticket
        link.remote_status, link.remote_closed = st["status"], st["closed"]

    for c in (await cl.comments(link.external_id, link.remote_cursor))[:MAX_NEW_COMMENTS]:
        if c["text"].startswith("[SOC Hub]"):  # our own push coming back
            link.remote_cursor = c["id"]
            continue
        db.add(TimelineEvent(case_id=case.id, event_type="ticket_comment",
                             content=f"**{c['author']}** in {label} {key} (task *{task.title}*):\n\n{c['text'][:MAX_TEXT]}"))
        link.remote_cursor = c["id"]

    if task.status != link.pushed_case_status:
        if task.status == "done" and not link.remote_closed:
            result = f": {_plain(task.result)}" if task.result else ""
            await cl.add_comment(link.external_id, f"[SOC Hub] Task completed{result}")
        elif task.status == "todo" and link.pushed_case_status == "done":
            await cl.add_comment(link.external_id, "[SOC Hub] Task reopened.")
        link.pushed_case_status = task.status
    link.last_synced_at = datetime.now(timezone.utc)
    await db.flush()


def _reason(e: Exception) -> str:
    if isinstance(e, TicketError):
        return str(e)
    logger.exception("ticket sync failed")
    return f"unexpected {type(e).__name__}"


async def sync_tenant(db: AsyncSession, conn: TicketConnection) -> dict:
    """Auto-create task tickets, then sync every task ticket until both sides are done. One failing ticket doesn't
    stop the others; errors land in the connection's last_error."""
    snap = snapshot(conn)
    snap.credentials_enc = conn.credentials_enc
    cl = client(snap, await _allowlist(db, snap.tenant_id))
    created = synced = 0
    errors = []
    if snap.auto_create_min_severity and snap.auto_create_since:
        # open tasks added after the setting was turned on, of open cases at/above the severity floor
        floor = SEVERITY_RANK.get(snap.auto_create_min_severity, 9)
        linked = select(CaseLink.task_id).where(CaseLink.tenant_id == snap.tenant_id, CaseLink.task_id.isnot(None))
        rows = (await db.execute(select(CaseTask.id, Case.id, Case.severity).join(Case, Case.id == CaseTask.case_id).where(
            CaseTask.tenant_id == snap.tenant_id, CaseTask.status == "todo", CaseTask.created_at >= snap.auto_create_since,
            Case.status.notin_([CaseStatus.RESOLVED, CaseStatus.CLOSED]), CaseTask.id.notin_(linked))
            .order_by(CaseTask.id).limit(MAX_AUTO_CREATE))).all()
        for task_id, case_id, sev in rows:
            if SEVERITY_RANK.get(getattr(sev, "value", sev), -1) < floor:
                continue
            try:
                await create_ticket(db, conn=snap, case=await db.get(Case, case_id), task=await db.get(CaseTask, task_id),
                                    user_id=None, cl=cl)
                await db.commit()
                created += 1
            except Exception as e:  # one bad task must not stop the rest
                await db.rollback()
                errors.append(f"task {task_id}: {_reason(e)}")
    rows = (await db.execute(select(CaseLink.id, CaseLink.case_id, CaseLink.task_id).where(
        CaseLink.tenant_id == snap.tenant_id, CaseLink.connection_id == snap.id, CaseLink.task_id.isnot(None)))).all()
    for link_id, case_id, task_id in rows:
        link, case, task = await db.get(CaseLink, link_id), await db.get(Case, case_id), await db.get(CaseTask, task_id)
        ext = link.external_id  # read now: a rollback expires `link`
        if task.status == "done" and link.pushed_case_status == "done" and link.remote_closed:
            continue  # both sides done
        try:
            await sync_link(db, cl, snap, link, case, task)
            await db.commit()
            synced += 1
        except Exception as e:
            await db.rollback()
            errors.append(f"ticket {ext}: {_reason(e)}")
    row = await db.get(TicketConnection, snap.id)
    row.last_synced_at = datetime.now(timezone.utc)
    row.last_error = "; ".join(errors)[:2000] or None
    await db.commit()
    return {"created": created, "synced": synced, "errors": errors}
