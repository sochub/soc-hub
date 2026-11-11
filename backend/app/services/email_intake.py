"""IMAP email intake: SSRF host guard, connection test, and the poller that turns each new email into
`email.received` workflow runs (or, as a fallback, a pending alert).

Retention: the .eml and its attachments are stored only when something consumed the email (a workflow fired or a
fallback alert was created); otherwise only the dedupe row in `mailbox_seen` is kept. A daily cleanup drops
unreferenced artifacts after 30 days and dedupe rows after 90.
"""
import asyncio
import hashlib
import imaplib
import logging
import re
import socket
import json
import ssl
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import List, Optional

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import delete, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.utils.hostguard import validate_outbound_host, vetted_ip
from app.models.case import Alert, Case
from app.models.email_artifact import EmailArtifact
from app.models.tenant import Tenant
from app.models.tenant_mailbox import MailboxSeen, TenantMailbox
from app.models.workflow import Workflow
from app.services import suppression_service
from app.services.attachments import clean_content_type, sanitize_filename, store_upload
from app.storage import get_storage
from app.utils.crypto import decrypt
from app.workflows.nodes.alerts import MAX_ALERT_PAYLOAD
from app.workflows.events import emit_event, trigger_matches
from app.workflows.runtime import create_run

logger = logging.getLogger(__name__)

BATCH_LIMIT = 50  # messages handled per mailbox per cycle
BODY_MAX_BYTES = 64 * 1024
MESSAGE_ID_MAX = 512  # mailbox_seen.message_id column width
PROCESSED_FOLDER = "Processed"
# poll_mailboxes_task runs with soft_time_limit=540 (app/tasks/email_intake.py). The sweep stops claiming new
# mailboxes at ~80% of it, and one mailbox's loop is bounded so a slow server can't starve the other tenants.
SWEEP_BUDGET_SECONDS = 432
MAILBOX_BUDGET_SECONDS = 120
CLEANUP_BATCH = 1000
SUBJECT_MAX_BYTES = 2048
ADDRESS_MAX_CHARS = 254
TO_MAX_ENTRIES = 100
TO_JOINED_MAX_BYTES = 4096
SKIPPED_MAX_ENTRIES = 20
ARTIFACT_RETENTION = timedelta(days=30)
SEEN_RETENTION = timedelta(days=90)

def validate_mailbox_host(host: str) -> Optional[str]:
    """Return an error message if `host` resolves to a non-public address (SSRF guard), else None.

    Exact hosts in settings.MAILBOX_PRIVATE_HOSTS are allowed."""
    return validate_outbound_host(host, settings.MAILBOX_PRIVATE_HOSTS, "MAILBOX_PRIVATE_HOSTS",
                                  resolve=lambda h: socket.getaddrinfo(h, None))


def _pinned(cls, ip: str):
    """`cls` (IMAP4 or IMAP4_SSL) connecting to the vetted `ip`, so a second DNS answer can't rebind the login onto
    an internal host. TLS (implicit or STARTTLS) still uses and verifies the configured name (self.host)."""
    class Pinned(cls):
        def _create_socket(self, timeout):
            sock = socket.create_connection((ip, self.port), timeout)
            ctx = getattr(self, "ssl_context", None)
            return ctx.wrap_socket(sock, server_hostname=self.host) if ctx is not None else sock
    return Pinned


def _fail(msg: str) -> dict:
    return {"ok": False, "message_count": None, "error": msg}


class MailboxError(Exception):
    """Carries only a generic, secret-free message (it ends up in `last_error` and API responses)."""


def _close(conn) -> None:
    try:
        conn.logout()
    except Exception:
        pass


def _open_connection(mailbox):
    """Connect and log in over verified TLS (implicit TLS, or STARTTLS and never cleartext). Shared by the
    connection test and the poller; tests patch this as the IMAP client factory. Raises MailboxError."""
    err, ip = vetted_ip(mailbox.host, settings.MAILBOX_PRIVATE_HOSTS, "MAILBOX_PRIVATE_HOSTS",
                        resolve=lambda h: socket.getaddrinfo(h, None))
    if err:
        raise MailboxError(err)
    conn = None
    try:
        password = decrypt(mailbox.password_enc)
        ctx = ssl.create_default_context()
        if mailbox.use_ssl:
            conn = _pinned(imaplib.IMAP4_SSL, ip)(mailbox.host, mailbox.port, ssl_context=ctx, timeout=10)
        else:
            conn = _pinned(imaplib.IMAP4, ip)(mailbox.host, mailbox.port, timeout=10)
            if "STARTTLS" not in getattr(conn, "capabilities", ()):
                raise MailboxError("server does not support STARTTLS")
            conn.starttls(ctx)  # never log in over cleartext
        conn.login(mailbox.username, password)
        return conn
    except MailboxError:
        if conn is not None:
            _close(conn)
        raise
    except ssl.SSLError:
        msg = "TLS handshake failed"
    except imaplib.IMAP4.error:
        msg = "authentication failed" if conn is not None and getattr(conn, "state", "") == "NONAUTH" else "IMAP error"
    except Exception:
        msg = "connection failed"
    if conn is not None:
        _close(conn)
    raise MailboxError(msg) from None


def test_connection(mailbox) -> dict:
    """Connect, log in over TLS, select the folder read-only and count messages.

    Errors are generic strings only (no exception text or server banners), so nothing internal or secret leaks.
    """
    try:
        conn = _open_connection(mailbox)
    except MailboxError as e:
        return _fail(str(e))
    try:
        typ, data = conn.select(mailbox.folder, readonly=True)
        if typ != "OK":
            return _fail("folder not found")
        return {"ok": True, "message_count": int(data[0]), "error": None}
    except imaplib.IMAP4.error:
        return _fail("IMAP error")
    except Exception:
        return _fail("connection failed")
    finally:
        _close(conn)


# ── parsing ──────────────────────────────────────────────────────────────────────────────────────

def _clean(value) -> str:
    """Postgres text/JSON reject NUL bytes."""
    return str(value or "").replace("\x00", "").strip()


class _TextOnly(HTMLParser):
    """Tag-stripper: drops script/style, keeps link targets (phishing hides them behind link text)."""
    BREAKS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BREAKS:
            self.parts.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if href.lower().startswith(("http://", "https://")):
                self.parts.append(f" {href} ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextOnly()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = "".join(p.parts)
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


def _part_text(part) -> str:
    try:
        return part.get_content()
    except Exception:  # unknown charset etc.
        raw = part.get_payload(decode=True) or b""
        return raw.decode("utf-8", errors="replace")


def _cap_bytes(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _addresses(*values) -> List[str]:
    out: List[str] = []
    for _, addr in getaddresses([str(v) for v in values if v]):
        addr = _clean(addr).lower()
        if addr and len(addr) <= ADDRESS_MAX_CHARS and addr not in out:
            out.append(addr)
    return out[:TO_MAX_ENTRIES]


@dataclass
class ParsedEmail:
    email: dict  # the payload's `email` block
    attachments: list = field(default_factory=list)  # [(filename, content_type, bytes)] within the size cap


def parse_email(raw: bytes) -> ParsedEmail:
    msg = message_from_bytes(raw, policy=policy.default)

    def hdr(name):
        try:
            return _clean(msg[name])
        except Exception:  # malformed header the policy refuses to decode
            return ""

    plain = html = None
    try:
        plain = msg.get_body(preferencelist=("plain",))
        html = msg.get_body(preferencelist=("html",))
    except Exception:
        pass
    text = _clean(_part_text(plain)) if plain is not None else ""
    if not text and html is not None:
        text = _clean(html_to_text(_part_text(html)))
    text = _cap_bytes(text, BODY_MAX_BYTES)

    sender = (_addresses(hdr("From")) or [""])[0]
    to = _addresses(hdr("To"), hdr("Cc"), hdr("Delivered-To"), hdr("X-Original-To"))
    subject = _cap_bytes(hdr("Subject"), SUBJECT_MAX_BYTES)
    try:
        date = parsedate_to_datetime(hdr("Date")).isoformat()
    except Exception:
        date = ""
    message_id = hdr("Message-ID")
    if not message_id:
        message_id = hashlib.sha256((sender + date + subject + text[:1024]).encode()).hexdigest()
    elif len(message_id) > MESSAGE_ID_MAX:
        message_id = hashlib.sha256(message_id.encode()).hexdigest()

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    attachments, skipped, found, inner = [], [], 0, set()
    for part in msg.walk():
        if id(part) in inner or part is plain or part is html:
            continue
        ctype = part.get_content_type()
        if ctype == "message/rfc822":  # an attached email: keep it whole, don't unpack its parts
            inner.update(id(p) for p in part.walk() if p is not part)
            data = b"".join(m.as_bytes() for m in part.get_payload())
        elif part.is_multipart():
            continue
        else:
            has_name = bool(part.get_filename())
            if part.get_content_maintype() == "text" and not has_name and part.get_content_disposition() != "attachment":
                continue  # an alternative body, not evidence
            data = part.get_payload(decode=True)
        if not data:
            continue
        found += 1
        name = sanitize_filename(_clean(part.get_filename()) or f"attachment-{found}")
        if len(data) > max_bytes:
            skipped.append({"filename": name, "size_bytes": len(data), "reason": "too large"})
            continue
        attachments.append((name, clean_content_type(ctype), data))

    return ParsedEmail(email={
        "message_id": message_id, "sender": sender, "to": to, "to_joined": _cap_bytes(", ".join(to), TO_JOINED_MAX_BYTES), "subject": subject,
        "date": date or None, "body_text": text, "has_attachments": found > 0, "attachment_count": found,
        "skipped_attachments": skipped[:SKIPPED_MAX_ENTRIES],
    }, attachments=attachments)


# ── IMAP I/O (blocking; always called through asyncio.to_thread) ─────────────────────────────────

def _imap_unseen(conn, folder: str) -> List[str]:
    typ, _ = conn.select(folder, readonly=False)
    if typ != "OK":
        raise MailboxError("folder not found")
    typ, data = conn.uid("SEARCH", None, "UNSEEN")
    if typ != "OK":
        raise MailboxError("IMAP error")
    return [u.decode() if isinstance(u, bytes) else u for u in (data[0] or b"").split()][:BATCH_LIMIT]


def _imap_fetch(conn, uid: str) -> Optional[bytes]:
    typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK: fetching must not mark the message seen
    if typ != "OK":
        return None
    for item in data or []:
        if isinstance(item, tuple):
            return item[1]
    return None


def _imap_archive(conn, uid: str) -> bool:
    """Mark seen, COPY to Processed (created on first use), and only after a successful COPY flag the original
    \\Deleted. Never a plain delete: if the copy fails the message stays in the inbox, merely marked seen."""
    conn.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    typ, _ = conn.uid("COPY", uid, PROCESSED_FOLDER)
    if typ != "OK":
        conn.create(PROCESSED_FOLDER)
        typ, _ = conn.uid("COPY", uid, PROCESSED_FOLDER)
    if typ != "OK":
        return False
    conn.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    return True


# ── polling ──────────────────────────────────────────────────────────────────────────────────────

async def _once(data: bytes):
    yield data


async def _store_blob(storage, db, *, tenant_id, mailbox_id, kind, filename, content_type, data, written) -> EmailArtifact:
    key = f"tenants/{tenant_id}/email/{uuid.uuid4().hex}"
    written.append(key)  # so a failed message can remove what it already wrote
    await storage.put(key, _once(data))
    row = EmailArtifact(tenant_id=tenant_id, mailbox_id=mailbox_id, kind=kind, filename=filename[:255],
                        content_type=content_type, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                        storage_key=key)
    db.add(row)
    return row


def _fit_payload(payload: dict, limit: int = MAX_ALERT_PAYLOAD) -> None:
    """Keep the payload under the same size guard alert_create enforces (it flows into run steps, audit rows and the
    fallback alert). The caps in parse_email make this a backstop; if it ever trips, shorten body_text and mark it."""
    reserve = 1024  # room for the artifact ids added later
    size = len(json.dumps(payload, default=str))
    if size + reserve <= limit:
        return
    mail = payload["email"]
    mail["truncated"] = True
    over = size + len('"truncated": true, ') + reserve - limit
    body = mail["body_text"].encode("utf-8")
    mail["body_text"] = body[:max(0, len(body) - over)].decode("utf-8", errors="ignore")
    if len(json.dumps(payload, default=str)) + reserve > limit:  # still too big: drop the bulky list fields too
        mail.update(body_text="", to=[], to_joined="", skipped_attachments=[])


async def _process_message(db, *, mailbox_id: int, tenant_id: int, fallback: bool, raw: bytes) -> bool:
    """Handle one email in one transaction. Returns False for a duplicate, True when newly processed."""
    from app.tasks.workflows import advance_run_task  # late: the worker imports this module's task wrapper

    parsed = parse_email(raw)
    mail = parsed.email
    first = (await db.execute(
        insert(MailboxSeen).values(mailbox_id=mailbox_id, message_id=mail["message_id"])
        .on_conflict_do_nothing(constraint="uq_mailbox_seen_mailbox_message").returning(MailboxSeen.id)
    )).scalar()
    if first is None:
        await db.rollback()
        return False

    payload = {"event": "email.received", "mailbox_id": mailbox_id, "email": mail}
    _fit_payload(payload)
    workflows = (await db.execute(select(Workflow).where(
        Workflow.tenant_id == tenant_id, Workflow.enabled == True,  # noqa: E712
        Workflow.trigger_type == "email.received").order_by(Workflow.id))).scalars().all()
    ctx = {"trigger": payload, "email": mail}
    matched = [wf for wf in workflows if trigger_matches(wf.trigger_filter, ctx)]

    run_ids: List[int] = []
    alert_id: Optional[int] = None
    suppressed = None
    written: List[str] = []
    storage = get_storage()
    try:
        if matched or fallback:  # retention rule: keep the evidence only when something consumes it
            subject_name = sanitize_filename(mail["subject"] or "message")[:200]
            rows = [await _store_blob(storage, db, tenant_id=tenant_id, mailbox_id=mailbox_id, kind="eml",
                                      filename=f"{subject_name}.eml", content_type="message/rfc822", data=raw,
                                      written=written)]
            for name, ctype, data in parsed.attachments:
                rows.append(await _store_blob(storage, db, tenant_id=tenant_id, mailbox_id=mailbox_id,
                                              kind="attachment", filename=name, content_type=ctype, data=data,
                                              written=written))
            await db.flush()
            payload["eml_artifact_id"] = rows[0].id
            payload["attachment_artifact_ids"] = [r.id for r in rows[1:]]

            if matched:
                for wf in matched:
                    run = await create_run(db, wf, trigger_payload=payload, case_id=None, alert_id=None, depth=0)
                    run_ids.append(run.id)
            else:
                alert_id = (await db.execute(
                    insert(Alert).values(
                        source="email", external_id=mail["message_id"], title=(mail["subject"] or "(no subject)")[:255],
                        payload=payload, status="pending", severity="medium", tenant_id=tenant_id)
                    .on_conflict_do_nothing(constraint="uq_alerts_tenant_source_external").returning(Alert.id)
                )).scalar()
                if alert_id is not None:
                    alert = (await db.execute(select(Alert).where(Alert.id == alert_id))).scalars().one()
                    suppressed = await suppression_service.apply(db, alert)
                    for r in rows:
                        r.alert_id = alert_id
                else:  # same message already alerted via another mailbox of this tenant
                    logger.info("email intake: alert already exists mailbox=%s", mailbox_id)
        await db.commit()
    except BaseException:
        await db.rollback()
        for key in written:
            try:
                await storage.delete(key)
            except Exception:
                logger.warning("email intake: blob cleanup failed mailbox=%s", mailbox_id)
        raise
    for rid in run_ids:
        advance_run_task.delay(rid)
    if alert_id is not None and suppressed is None:
        emit_event(tenant_id, "alert.ingested", alert_id=alert_id)
    return True


async def poll_mailbox(db, mailbox, *, deadline: Optional[float] = None) -> int:
    """Fetch up to BATCH_LIMIT unseen emails and process each; returns how many were new (not duplicates).

    Each message commits on its own, then is archived on the server. A message that fails to process stays
    unseen (retried next cycle); the others still go through and a MailboxError summarises the failures.
    `deadline` (time.monotonic) stops the loop between messages; the rest stay unseen for the next cycle.
    SoftTimeLimitExceeded is never swallowed."""
    await db.refresh(mailbox)  # a rollback on an earlier message may have expired it
    mailbox_id, tenant_id, fallback, folder = mailbox.id, mailbox.tenant_id, mailbox.fallback_create_alert, mailbox.folder
    conn = await asyncio.to_thread(_open_connection, mailbox)
    new = failed = 0
    archived = False
    try:
        try:
            uids = await asyncio.to_thread(_imap_unseen, conn, folder)
        except (MailboxError, SoftTimeLimitExceeded):
            raise
        except Exception:
            raise MailboxError("IMAP error") from None
        for uid in uids:
            if deadline is not None and time.monotonic() > deadline:
                break
            raw = await asyncio.to_thread(_imap_fetch, conn, uid)
            if raw is None:
                failed += 1
                continue
            try:
                new += await _process_message(db, mailbox_id=mailbox_id, tenant_id=tenant_id, fallback=fallback,
                                              raw=raw)
            except SoftTimeLimitExceeded:
                raise
            except Exception as e:  # never log message content or addresses
                logger.warning("email intake: message failed mailbox=%s err=%s", mailbox_id, type(e).__name__)
                failed += 1
                continue
            archived |= await asyncio.to_thread(_imap_archive, conn, uid)
        if archived:
            await asyncio.to_thread(conn.expunge)
    finally:
        await asyncio.to_thread(_close, conn)
    if failed:
        raise MailboxError(f"{failed} message(s) could not be processed")
    return new


async def _claim_next(now: datetime, mailbox_ids=None) -> Optional[int]:
    """Claim the most overdue due mailbox under FOR UPDATE SKIP LOCKED, stamping last_polled_at in the same short
    transaction. The stamp is the claim: per-message commits of a long poll release row locks, so an overlapping
    sweep must find the mailbox not due (and mailbox_seen's unique key is the real duplicate guard). One at a
    time, so a sweep that runs out of budget leaves the remaining mailboxes due (and first in line) next cycle."""
    async with AsyncSessionLocal() as db:
        due = (TenantMailbox.last_polled_at.is_(None)) | (
            TenantMailbox.last_polled_at + TenantMailbox.poll_interval_seconds * literal_column("interval '1 second'")
            <= now)
        q = select(TenantMailbox.id).where(
            TenantMailbox.enabled == True, due,  # noqa: E712
            TenantMailbox.tenant_id.in_(select(Tenant.id).where(Tenant.is_active.isnot(False))),
        ).order_by(TenantMailbox.last_polled_at.asc().nullsfirst(), TenantMailbox.id).limit(1).with_for_update(
            skip_locked=True)
        if mailbox_ids is not None:
            q = q.where(TenantMailbox.id.in_(mailbox_ids))
        mid = (await db.execute(q)).scalar()
        if mid is not None:
            await db.execute(update(TenantMailbox).where(TenantMailbox.id == mid).values(last_polled_at=now))
        await db.commit()
        return mid


async def poll_due_mailboxes(now: Optional[datetime] = None, *, mailbox_ids=None,
                             budget: float = SWEEP_BUDGET_SECONDS,
                             mailbox_budget: float = MAILBOX_BUDGET_SECONDS) -> List[int]:
    """Beat sweep: poll due mailboxes until none is left or `budget` seconds are used. One mailbox failing never
    aborts the others; its `last_error` is a generic string (never the password or an exception's text). Each
    mailbox gets at most `mailbox_budget` seconds; unfetched messages stay unseen for the next cycle.
    `mailbox_ids` scopes the sweep (tests)."""
    now = now or datetime.now(timezone.utc)
    sweep_deadline = time.monotonic() + budget
    polled: List[int] = []
    while time.monotonic() < sweep_deadline:
        mid = await _claim_next(now, mailbox_ids)
        if mid is None:
            break
        polled.append(mid)
        error: Optional[str] = None
        try:
            async with AsyncSessionLocal() as db:
                mb = await db.get(TenantMailbox, mid)
                if mb is None or not mb.enabled:
                    continue
                await poll_mailbox(db, mb, deadline=min(sweep_deadline, time.monotonic() + mailbox_budget))
        except SoftTimeLimitExceeded:
            raise
        except MailboxError as e:
            error = str(e)
        except Exception as e:
            logger.warning("email intake: poll failed mailbox=%s err=%s", mid, type(e).__name__)
            error = "poll failed"
        async with AsyncSessionLocal() as db:
            await db.execute(update(TenantMailbox).where(TenantMailbox.id == mid).values(
                last_error=error, last_polled_at=datetime.now(timezone.utc)))
            await db.commit()
    return polled


# ── promotion: copy the stored evidence into the case ────────────────────────────────────────────

def evidence_ids(payload) -> List[int]:
    if not isinstance(payload, dict):
        return []
    atts = payload.get("attachment_artifact_ids")
    raw = [payload.get("eml_artifact_id"), *(atts if isinstance(atts, list) else [])]
    return [i for i in raw if isinstance(i, int) and not isinstance(i, bool)]


async def link_alert_artifacts(db, *, alert_id: int, tenant_id: int, payload) -> None:
    """Hold this tenant's still-unclaimed email artifacts named in `payload` on the alert, so the 30-day cleanup
    keeps them (same as the fallback path). Used by the alert_create node. Flushes only; the caller commits."""
    ids = evidence_ids(payload)
    if ids:
        await db.execute(update(EmailArtifact).where(
            EmailArtifact.id.in_(ids), EmailArtifact.tenant_id == tenant_id, EmailArtifact.alert_id.is_(None)
        ).values(alert_id=alert_id))


async def copy_evidence_for_alert(alert_id: int, case_id: int, user_id: Optional[int] = None) -> int:
    """copy_email_evidence for a promotion done by automation (runs from a Celery task after the step commits)."""
    async with AsyncSessionLocal() as db:
        alert = (await db.execute(select(Alert).where(Alert.id == alert_id))).scalars().first()
    if alert is None:
        return 0
    return await copy_email_evidence(alert=alert, case_id=case_id, user_id=user_id)


async def copy_email_evidence(*, alert, case_id: int, user_id: Optional[int]) -> int:
    """After an email alert is promoted (and that commit is done), copy its .eml and attachments into the case as
    regular evidence and link each EmailArtifact to the new attachment (so retention keeps them). Own session:
    store_upload commits, and a failure of one file must not touch the caller's objects. Best effort; returns
    the number copied. Only this tenant's artifacts that are unlinked and not another alert's are eligible."""
    ids = evidence_ids(alert.payload)
    if not ids:
        return 0
    copied = 0
    storage = get_storage()
    async with AsyncSessionLocal() as db:
        case = (await db.execute(select(Case).where(Case.id == case_id, Case.tenant_id == alert.tenant_id))).scalars().first()
        if case is None:
            return 0
        items = [(r.id, r.filename, r.content_type, r.storage_key, r.kind) for r in (await db.execute(
            select(EmailArtifact).where(
                EmailArtifact.id.in_(ids), EmailArtifact.tenant_id == alert.tenant_id,
                EmailArtifact.case_attachment_id.is_(None),
                (EmailArtifact.alert_id.is_(None)) | (EmailArtifact.alert_id == alert.id)
            ).order_by(EmailArtifact.id))).scalars().all()]
        for art_id, filename, ctype, key, kind in items:
            try:
                att = await store_upload(
                    db, case=case, user_id=user_id, filename=filename, content_type=ctype,
                    chunks=storage.open(key), is_malicious=False, storage=storage,
                    description="Original email (from email intake)" if kind == "eml" else "Email attachment (from email intake)")
                await db.execute(update(EmailArtifact).where(EmailArtifact.id == art_id).values(
                    case_attachment_id=att.id))
                await db.commit()
                copied += 1
            except SoftTimeLimitExceeded:
                raise
            except Exception as e:  # store_upload already rolled back; the evidence stays in email_artifacts
                logger.warning("email evidence copy failed artifact=%s err=%s", art_id, type(e).__name__)
                case = (await db.execute(select(Case).where(Case.id == case_id))).scalars().first()
    return copied


# ── retention ────────────────────────────────────────────────────────────────────────────────────

async def cleanup_email_artifacts(now: Optional[datetime] = None, *, tenant_ids=None) -> dict:
    """Delete email artifacts (blob first, then row) older than 30 days that no alert or case holds, and dedupe
    rows older than 90 days. Works through them in CLEANUP_BATCH-sized keyset batches until none is left, so a busy
    tenant never outgrows one daily run. `tenant_ids` scopes the run (tests)."""
    now = now or datetime.now(timezone.utc)
    storage = get_storage()
    removed = 0
    last_id = 0
    async with AsyncSessionLocal() as db:
        while True:
            q = select(EmailArtifact.id, EmailArtifact.storage_key).where(
                EmailArtifact.id > last_id, EmailArtifact.created_at < now - ARTIFACT_RETENTION,
                EmailArtifact.alert_id.is_(None), EmailArtifact.case_attachment_id.is_(None)
            ).order_by(EmailArtifact.id).limit(CLEANUP_BATCH)
            if tenant_ids is not None:
                q = q.where(EmailArtifact.tenant_id.in_(tenant_ids))
            batch = (await db.execute(q)).all()
            for art_id, key in batch:
                last_id = art_id
                try:
                    await storage.delete(key)  # idempotent; a failure leaves the row, so the next run retries it
                except SoftTimeLimitExceeded:
                    raise
                except Exception:
                    logger.warning("email artifact blob delete failed id=%s", art_id)
                    continue
                await db.execute(delete(EmailArtifact).where(EmailArtifact.id == art_id))
                removed += 1
            await db.commit()
            if len(batch) < CLEANUP_BATCH:
                break
        sq = delete(MailboxSeen).where(MailboxSeen.processed_at < now - SEEN_RETENTION)
        if tenant_ids is not None:
            sq = sq.where(MailboxSeen.mailbox_id.in_(select(TenantMailbox.id).where(TenantMailbox.tenant_id.in_(tenant_ids))))
        seen = (await db.execute(sq)).rowcount
        await db.commit()
    return {"artifacts": removed, "seen": seen}
