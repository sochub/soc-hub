"""Email intake: IMAP polling fires email.received workflows or falls back to an alert; retention and cleanup.

The IMAP client is faked by patching `email_intake._open_connection`; storage points at tmp_path. Every sweep and
cleanup is scoped to ids created here (the dev DB may hold real mailboxes), and cleanup deletes explicit ids only."""
import asyncio
import contextlib
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import delete, func, select, update

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact
from app.models.audit_log import AuditLog
from app.models.case import Alert, Case, TimelineEvent
from app.models.case_attachment import CaseAttachment
from app.models.email_artifact import EmailArtifact
from app.models.tenant import Tenant
from app.models.tenant_mailbox import MailboxSeen, TenantMailbox
from app.models.workflow import Workflow, WorkflowRun, WorkflowRunStep
from app.services import email_intake
from app.utils.crypto import encrypt
from app.workflows.validation import validate_graph, workflow_errors

GRAPH = {"nodes": [{"id": "start", "type": "trigger", "position": {"x": 0, "y": 0}, "config": {}}], "edges": []}
PDF = b"%PDF-1.4 fake attachment bytes"


def make_mail(*, mid="<m1@example.test>", subject="Suspicious invoice", to="soc+phishing@example.test",
              sender="Eve <eve@evil.test>", body="Please pay now http://evil.test/pay", html=None,
              attachments=(), date="Mon, 09 Nov 2026 10:00:00 +0000") -> bytes:
    m = EmailMessage()
    if mid:
        m["Message-ID"] = mid
    m["From"], m["To"], m["Subject"] = sender, to, subject
    if date:
        m["Date"] = date
    if html is not None and body is None:
        m.set_content("fallback")
        m.clear_content()
        m.set_content(html, subtype="html")
    else:
        m.set_content(body)
        if html is not None:
            m.add_alternative(html, subtype="html")
    for name, data, ctype in attachments:
        maintype, subtype = ctype.split("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return m.as_bytes()


class FakeIMAP:
    """Just enough of imaplib for the poller. `copy_fails` models a server where COPY never works."""
    capabilities = ("IMAP4REV1",)

    def __init__(self, messages, copy_fails=False):
        self.messages = dict(messages)  # uid(bytes-str) -> raw bytes
        self.unseen = list(self.messages)
        self.folders = set()
        self.copy_fails = copy_fails
        self.log = []
        self.copied, self.deleted, self.seen, self.fetched = [], [], [], []

    def select(self, folder, readonly=False):
        self.log.append(("select", folder, readonly))
        return "OK", [b"1"]

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        self.log.append(("uid", cmd) + tuple(str(a) for a in args))
        if cmd == "SEARCH":
            return "OK", [" ".join(u for u in self.unseen if u not in self.seen).encode()]
        if cmd == "FETCH" and args[1] == "(RFC822.SIZE)":
            uid = args[0]
            return "OK", [b"1 (UID %s RFC822.SIZE %d)" % (uid.encode(), len(self.messages[uid]))]
        if cmd == "FETCH":
            uid = args[0]
            self.fetched.append(uid)
            assert "PEEK" in args[1], "must not mark messages seen on fetch"
            return "OK", [(b"1 (UID %s BODY[] {1}" % uid.encode(), self.messages[uid]), b")"]
        if cmd == "STORE":
            uid, _, flag = args
            (self.seen if "Seen" in flag else self.deleted).append(uid)
            return "OK", [b""]
        if cmd == "COPY":
            uid, dest = args
            if self.copy_fails or dest not in self.folders:
                return "NO", [b"[TRYCREATE] no such mailbox"]
            self.copied.append((uid, dest))
            return "OK", [b""]
        raise AssertionError(f"unexpected uid command {cmd}")

    def create(self, name):
        self.log.append(("create", name))
        self.folders.add(name)
        return "OK", [b""]

    def expunge(self):
        self.log.append(("expunge",))
        return "OK", [b""]

    def logout(self):
        self.log.append(("logout",))


class Env:
    def __init__(self):
        self.h = secrets.token_hex(3)
        self.tenants, self.mailboxes, self.workflows, self.cases = [], [], [], []
        self.delayed, self.events = [], []
        self.fake = None


@contextlib.asynccontextmanager
async def env(monkeypatch, tmp_path, *, fallback=False, messages=(), **fake_kw):
    await engine.dispose()
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 1)
    import app.tasks.workflows as tw
    e = Env()
    monkeypatch.setattr(tw.advance_run_task, "delay", lambda rid: e.delayed.append(rid))
    monkeypatch.setattr(tw.dispatch_event_task, "delay", lambda *a: e.events.append(a))
    e.fake = FakeIMAP({str(i + 1): m for i, m in enumerate(messages)}, **fake_kw)
    monkeypatch.setattr(email_intake, "_open_connection", lambda mb: e.fake)
    async with AsyncSessionLocal() as db:
        try:
            t = Tenant(name="email-test", slug=f"email-{e.h}")
            db.add(t)
            await db.flush()
            e.tenants.append(int(t.id))
            mb = TenantMailbox(tenant_id=t.id, name="Phishing", host="imap.example.test", username="soc",
                               password_enc=encrypt("pw-secret-xyz"), fallback_create_alert=fallback)
            db.add(mb)
            await db.flush()
            e.mailboxes.append(int(mb.id))
            await db.commit()
            e.tid, e.mid = e.tenants[0], e.mailboxes[0]
            yield e, db, mb
        finally:
            await db.rollback()
            tids, cids = e.tenants, e.cases
            run_ids = (await db.execute(select(WorkflowRun.id).where(WorkflowRun.tenant_id.in_(tids)))).scalars().all()
            await db.execute(delete(EmailArtifact).where(EmailArtifact.tenant_id.in_(tids)))
            if cids:
                art_ids = (await db.execute(select(Artifact.id).where(Artifact.tenant_id.in_(tids)))).scalars().all()
                await db.execute(delete(CaseAttachment).where(CaseAttachment.case_id.in_(cids)))
                await db.execute(delete(TimelineEvent).where(TimelineEvent.case_id.in_(cids)))
                from app.models.case_artifact import CaseArtifact
                await db.execute(delete(CaseArtifact).where(CaseArtifact.case_id.in_(cids)))
                if art_ids:
                    await db.execute(delete(Artifact).where(Artifact.id.in_(art_ids)))
            await db.execute(delete(Alert).where(Alert.tenant_id.in_(tids)))
            if cids:
                await db.execute(delete(Case).where(Case.id.in_(cids)))
            await db.execute(delete(WorkflowRunStep).where(WorkflowRunStep.run_id.in_(run_ids)))
            await db.execute(delete(WorkflowRun).where(WorkflowRun.id.in_(run_ids)))
            await db.execute(delete(Workflow).where(Workflow.tenant_id.in_(tids)))
            await db.execute(delete(AuditLog).where(AuditLog.tenant_id.in_(tids)))
            await db.execute(delete(TenantMailbox).where(TenantMailbox.tenant_id.in_(tids)))
            await db.execute(delete(Tenant).where(Tenant.id.in_(tids)))
            await db.commit()
    await engine.dispose()


async def add_workflow(db, e, *, trigger_filter="", enabled=True, trigger_type="email.received"):
    wf = Workflow(tenant_id=e.tid, name=f"wf-{len(e.workflows)}", enabled=enabled, trigger_type=trigger_type,
                  trigger_filter=trigger_filter, graph=GRAPH)
    db.add(wf)
    await db.flush()
    e.workflows.append(int(wf.id))
    await db.commit()
    return wf


async def artifacts(db, e):
    return (await db.execute(select(EmailArtifact).where(EmailArtifact.tenant_id == e.tid)
                             .order_by(EmailArtifact.id))).scalars().all()


async def seen_ids(db, e):
    return set((await db.execute(select(MailboxSeen.message_id).where(MailboxSeen.mailbox_id == e.mid))).scalars())


def run(coro):
    asyncio.run(coro)


def test_matching_workflow_creates_run_with_payload_and_artifacts(monkeypatch, tmp_path):
    raw = make_mail(html="<p>Pay <a href='http://evil.test/pay'>here</a></p>",
                    attachments=[("invoice.pdf", PDF, "application/pdf")])

    async def body():
        async with env(monkeypatch, tmp_path, messages=[raw]) as (e, db, mb):
            hit = await add_workflow(db, e, trigger_filter="'phishing' in email.to_joined")
            await add_workflow(db, e, trigger_filter="'nothing' in email.subject")
            await add_workflow(db, e, enabled=False)
            await add_workflow(db, e, trigger_type="alert.ingested")

            assert await email_intake.poll_mailbox(db, mb) == 1

            runs = (await db.execute(select(WorkflowRun).where(WorkflowRun.tenant_id == e.tid))).scalars().all()
            assert [r.workflow_id for r in runs] == [hit.id] and e.delayed == [runs[0].id]
            p = runs[0].trigger_payload
            arts = await artifacts(db, e)
            eml = next(a for a in arts if a.kind == "eml")
            att = next(a for a in arts if a.kind == "attachment")
            assert p["event"] == "email.received" and p["mailbox_id"] == e.mid
            assert p["eml_artifact_id"] == eml.id and p["attachment_artifact_ids"] == [att.id]
            m = p["email"]
            assert m["message_id"] == "<m1@example.test>" and m["sender"] == "eve@evil.test"
            assert m["to"] == ["soc+phishing@example.test"] and m["to_joined"] == "soc+phishing@example.test"
            assert m["subject"] == "Suspicious invoice" and m["date"].startswith("2026-11-09T10:00:00")
            assert m["has_attachments"] is True and m["attachment_count"] == 1
            assert "Please pay now" in m["body_text"] and "<" not in m["body_text"]
            assert runs[0].case_id is None and runs[0].alert_id is None
            # blobs landed in storage and match their rows
            assert eml.alert_id is None and eml.case_attachment_id is None and eml.mailbox_id == e.mid
            assert eml.storage_key.startswith(f"tenants/{e.tid}/email/")
            assert (tmp_path / eml.storage_key).read_bytes() == raw
            assert (tmp_path / att.storage_key).read_bytes() == PDF
            assert att.filename == "invoice.pdf" and att.sha256 == hashlib.sha256(PDF).hexdigest()
            assert att.content_type == "application/pdf" and att.size_bytes == len(PDF)
            assert await seen_ids(db, e) == {"<m1@example.test>"}
            assert (await db.execute(select(func.count()).select_from(Alert).where(Alert.tenant_id == e.tid))).scalar() == 0
            # the message is archived, never plain-deleted
            f = e.fake
            assert f.seen == ["1"] and f.copied == [("1", "Processed")] and f.deleted == ["1"]
            assert ("create", "Processed") in f.log and ("expunge",) in f.log
            assert f.log.index(("expunge",)) > max(i for i, x in enumerate(f.log) if x[:2] == ("uid", "STORE"))
            assert ("select", "INBOX", False) in f.log
    run(body())


def test_no_match_with_fallback_creates_pending_alert_with_linked_artifacts(monkeypatch, tmp_path):
    raw = make_mail(attachments=[("a.txt", b"hello", "text/plain")])

    async def body():
        async with env(monkeypatch, tmp_path, fallback=True, messages=[raw]) as (e, db, mb):
            await add_workflow(db, e, trigger_filter="'nothing' in email.subject")
            assert await email_intake.poll_mailbox(db, mb) == 1
            alert = (await db.execute(select(Alert).where(Alert.tenant_id == e.tid))).scalars().one()
            assert (alert.source, alert.external_id, alert.status, alert.severity) == \
                ("email", "<m1@example.test>", "pending", "medium")
            assert alert.title == "Suspicious invoice"
            arts = await artifacts(db, e)
            assert len(arts) == 2 and {a.alert_id for a in arts} == {alert.id}
            assert alert.payload["eml_artifact_id"] in {a.id for a in arts}
            assert alert.payload["email"]["sender"] == "eve@evil.test"
            assert len(e.events) == 1 and e.events[0][:2] == (e.tid, "alert.ingested") and e.events[0][3] == alert.id
            assert (await db.execute(select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.tenant_id == e.tid))).scalar() == 0 and e.delayed == []
    run(body())


def test_matching_workflow_wins_over_fallback(monkeypatch, tmp_path):
    async def body():
        async with env(monkeypatch, tmp_path, fallback=True, messages=[make_mail()]) as (e, db, mb):
            await add_workflow(db, e)
            await email_intake.poll_mailbox(db, mb)
            assert (await db.execute(select(func.count()).select_from(Alert).where(Alert.tenant_id == e.tid))).scalar() == 0
            assert len(e.delayed) == 1
    run(body())


def test_no_match_no_fallback_stores_only_seen_row(monkeypatch, tmp_path):
    raw = make_mail(attachments=[("a.txt", b"hello", "text/plain")])

    async def body():
        async with env(monkeypatch, tmp_path, messages=[raw]) as (e, db, mb):
            await add_workflow(db, e, trigger_filter="'nothing' in email.subject")
            assert await email_intake.poll_mailbox(db, mb) == 1
            assert await artifacts(db, e) == [] and await seen_ids(db, e) == {"<m1@example.test>"}
            assert (await db.execute(select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.tenant_id == e.tid))).scalar() == 0
            assert not [p for p in tmp_path.rglob("*") if p.is_file()]
            assert e.fake.copied == [("1", "Processed")] and e.fake.deleted == ["1"]  # still archived
    run(body())


def test_duplicate_message_is_noop_but_still_archived(monkeypatch, tmp_path):
    raw = make_mail()

    async def body():
        async with env(monkeypatch, tmp_path, messages=[raw]) as (e, db, mb):
            await add_workflow(db, e)
            assert await email_intake.poll_mailbox(db, mb) == 1
            e.fake = FakeIMAP({"7": raw})  # the same message shows up again as unseen
            assert await email_intake.poll_mailbox(db, mb) == 0
            assert (await db.execute(select(func.count()).select_from(WorkflowRun).where(
                WorkflowRun.tenant_id == e.tid))).scalar() == 1 and len(await artifacts(db, e)) == 1
            assert e.fake.deleted == ["7"] and len(e.delayed) == 1
    run(body())


def test_cap_of_50_per_cycle(monkeypatch, tmp_path):
    msgs = [make_mail(mid=f"<m{i}@example.test>") for i in range(60)]

    async def body():
        async with env(monkeypatch, tmp_path, messages=msgs) as (e, db, mb):
            assert await email_intake.poll_mailbox(db, mb) == 50
            assert len(await seen_ids(db, e)) == 50
            f = e.fake
            assert len(f.fetched) == 50 and len(f.deleted) == 50 and len(f.seen) == 50
            untouched = {str(i) for i in range(1, 61)} - set(f.fetched)
            assert len(untouched) == 10 and not untouched & set(f.deleted)
            assert f.fetched[0] == "1" and f.fetched[-1] == "50"  # oldest first
            assert await email_intake.poll_mailbox(db, mb) == 10  # next cycle drains the rest
    run(body())


def test_failing_message_rolls_back_cleans_blobs_and_leaves_others(monkeypatch, tmp_path):
    import pytest
    good = make_mail(mid="<good@example.test>", attachments=[("g.txt", b"good", "text/plain")])
    bad = make_mail(mid="<bad@example.test>", attachments=[("b.txt", b"bad", "text/plain")])
    real = email_intake.create_run

    async def flaky(db, wf, **kw):
        if kw["trigger_payload"]["email"]["message_id"] == "<bad@example.test>":
            raise RuntimeError("boom secret detail")
        return await real(db, wf, **kw)
    monkeypatch.setattr(email_intake, "create_run", flaky)

    async def body():
        async with env(monkeypatch, tmp_path, messages=[good, bad]) as (e, db, mb):
            await add_workflow(db, e)
            with pytest.raises(email_intake.MailboxError) as exc:
                await email_intake.poll_mailbox(db, mb)
            assert str(exc.value) == "1 message(s) could not be processed" and "boom" not in str(exc.value)
            assert await seen_ids(db, e) == {"<good@example.test>"}
            assert sorted(a.filename for a in await artifacts(db, e) if a.kind == "attachment") == ["g.txt"]
            assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 2  # the bad message's blobs are gone
            f = e.fake
            assert f.seen == ["1"] and f.deleted == ["1"] and f.copied == [("1", "Processed")]  # bad stays unseen
            assert len(e.delayed) == 1
    run(body())


def test_filter_on_missing_header_is_no_match_not_a_crash(monkeypatch, tmp_path):
    async def body():
        async with env(monkeypatch, tmp_path, messages=[make_mail(date=None)]) as (e, db, mb):
            await add_workflow(db, e, trigger_filter="email.date.startswith('2026')")
            await add_workflow(db, e, trigger_filter="email.date > 'x'")
            assert await email_intake.poll_mailbox(db, mb) == 1
            assert e.delayed == [] and await seen_ids(db, e) == {"<m1@example.test>"}
    run(body())


def test_failed_copy_never_deletes_or_expunges(monkeypatch, tmp_path):
    async def body():
        async with env(monkeypatch, tmp_path, messages=[make_mail()], copy_fails=True) as (e, db, mb):
            await add_workflow(db, e)
            assert await email_intake.poll_mailbox(db, mb) == 1
            f = e.fake
            assert f.copied == [] and f.deleted == [] and ("expunge",) not in f.log
            assert f.seen == ["1"]  # processed, so it is not re-fetched as unseen
    run(body())


def test_imap_auth_failure_sets_last_error_and_keeps_mailbox_enabled(monkeypatch, tmp_path):
    async def body():
        async with env(monkeypatch, tmp_path) as (e, db, mb):
            def boom(m):
                raise email_intake.MailboxError("authentication failed")
            monkeypatch.setattr(email_intake, "_open_connection", boom)
            await email_intake.poll_due_mailboxes(mailbox_ids=[e.mid])
            row = (await db.execute(select(TenantMailbox).where(TenantMailbox.id == e.mid))).scalars().one()
            await db.refresh(row)
            assert row.enabled is True and row.last_error == "authentication failed"
            assert row.last_polled_at is not None and "pw-secret-xyz" not in row.last_error
            # an unexpected exception is a generic string, never its text
            monkeypatch.setattr(email_intake, "_open_connection",
                                lambda m: (_ for _ in ()).throw(RuntimeError("secret internal pw-secret-xyz")))
            await db.execute(update(TenantMailbox).where(TenantMailbox.id == e.mid).values(last_polled_at=None))
            await db.commit()
            await email_intake.poll_due_mailboxes(mailbox_ids=[e.mid])
            await db.refresh(row)
            assert row.last_error and "pw-secret-xyz" not in row.last_error and "internal" not in row.last_error
    run(body())


def test_sweep_selects_only_due_enabled_active_mailboxes(monkeypatch, tmp_path):
    async def body():
        async with env(monkeypatch, tmp_path, messages=[make_mail()]) as (e, db, mb):
            await add_workflow(db, e)
            now = datetime.now(timezone.utc)
            mbx = lambda: select(TenantMailbox).where(TenantMailbox.id == e.mid)  # noqa: E731

            # polled a moment ago, interval 120s: not due
            await db.execute(update(TenantMailbox).where(TenantMailbox.id == e.mid).values(
                last_polled_at=now - timedelta(seconds=30)))
            await db.commit()
            await email_intake.poll_due_mailboxes(now, mailbox_ids=[e.mid])
            assert e.fake.fetched == [] and e.fake.log == []
            # due now
            await email_intake.poll_due_mailboxes(now + timedelta(seconds=100), mailbox_ids=[e.mid])
            assert e.fake.fetched == ["1"]
            row = (await db.execute(mbx())).scalars().one()
            await db.refresh(row)
            assert row.last_error is None and row.last_polled_at is not None
            # disabled: skipped
            e.fake = FakeIMAP({"1": make_mail(mid="<other@example.test>")})
            await db.execute(update(TenantMailbox).where(TenantMailbox.id == e.mid).values(
                enabled=False, last_polled_at=None))
            await db.commit()
            await email_intake.poll_due_mailboxes(now, mailbox_ids=[e.mid])
            assert e.fake.log == []
            # deactivated tenant: skipped
            await db.execute(update(TenantMailbox).where(TenantMailbox.id == e.mid).values(enabled=True))
            await db.execute(update(Tenant).where(Tenant.id == e.tid).values(is_active=False))
            await db.commit()
            await email_intake.poll_due_mailboxes(now, mailbox_ids=[e.mid])
            assert e.fake.log == []
    run(body())


def test_oversize_attachment_skipped_and_noted(monkeypatch, tmp_path):
    big = b"x" * (1024 * 1024 + 1)
    raw = make_mail(attachments=[("big.bin", big, "application/octet-stream"), ("ok.txt", b"ok", "text/plain")])

    async def body():
        async with env(monkeypatch, tmp_path, messages=[raw]) as (e, db, mb):
            await add_workflow(db, e)
            await email_intake.poll_mailbox(db, mb)
            r = (await db.execute(select(WorkflowRun).where(WorkflowRun.tenant_id == e.tid))).scalars().one()
            m = r.trigger_payload["email"]
            assert m["attachment_count"] == 2 and len(r.trigger_payload["attachment_artifact_ids"]) == 1
            assert m["skipped_attachments"] == [{"filename": "big.bin", "size_bytes": len(big), "reason": "too large"}]
            assert [a.filename for a in await artifacts(db, e) if a.kind == "attachment"] == ["ok.txt"]
    run(body())


def test_parse_email_html_only_missing_id_and_hostile_input():
    html = ("<html><head><style>p{color:red}</style></head><body><script>alert(1)</script>"
            "<p>Hello <a href='https://evil.test/x'>click</a></p><div>Bye</div></body></html>")
    p = email_intake.parse_email(make_mail(mid=None, body=None, html=html, subject="Hi\x00 there"))
    text = p.email["body_text"]
    assert "Hello" in text and "click" in text and "https://evil.test/x" in text and "Bye" in text
    assert "alert(1)" not in text and "color:red" not in text and "<" not in text
    assert "\x00" not in p.email["subject"]
    expect = hashlib.sha256(
        (p.email["sender"] + p.email["date"] + p.email["subject"] + text[:1024]).encode()).hexdigest()
    assert p.email["message_id"] == expect

    big = make_mail(body="é" * 100_000)
    assert len(email_intake.parse_email(big).email["body_text"].encode()) <= 65536
    long_id = email_intake.parse_email(make_mail(mid="<" + "a" * 600 + "@x>")).email["message_id"]
    assert len(long_id) <= 512


def test_validate_graph_accepts_email_trigger():
    assert validate_graph(GRAPH, "email.received") == []
    assert workflow_errors(GRAPH, "email.received", "'phishing' in email.to_joined") == []
    assert workflow_errors(GRAPH, "email.received", "'a' in") != []
    assert validate_graph(GRAPH, "email.bogus") != []


def test_copy_evidence_on_promotion_is_tenant_scoped(monkeypatch, tmp_path):
    raw = make_mail(attachments=[("a.txt", b"hello", "text/plain")])

    async def body():
        async with env(monkeypatch, tmp_path, fallback=True, messages=[raw]) as (e, db, mb):
            await email_intake.poll_mailbox(db, mb)
            alert = (await db.execute(select(Alert).where(Alert.tenant_id == e.tid))).scalars().one()
            case = Case(title="c", tenant_id=e.tid, description="d")
            db.add(case)
            await db.flush()
            e.cases.append(int(case.id))
            await db.commit()

            # a payload naming another tenant's artifact must not copy it
            other = Tenant(name="email-test", slug=f"email-{e.h}-o")
            db.add(other)
            await db.flush()
            e.tenants.append(int(other.id))
            foreign = EmailArtifact(tenant_id=other.id, kind="attachment", filename="secret.txt", size_bytes=3,
                                    sha256="0" * 64, storage_key=f"tenants/{other.id}/email/{secrets.token_hex(8)}")
            db.add(foreign)
            await db.commit()
            alert.payload = {**alert.payload, "attachment_artifact_ids": [*alert.payload["attachment_artifact_ids"],
                                                                         int(foreign.id)]}
            n = await email_intake.copy_email_evidence(alert=alert, case_id=case.id, user_id=None)
            assert n == 2
            atts = (await db.execute(select(CaseAttachment).where(CaseAttachment.case_id == case.id)
                                     .order_by(CaseAttachment.id))).scalars().all()
            assert sorted(a.filename for a in atts) == ["Suspicious invoice.eml", "a.txt"]
            assert all(not a.is_inline and not a.is_malicious for a in atts)
            for a in atts:
                assert (tmp_path / a.storage_key).exists() and "/cases/" in a.storage_key
            await db.commit()
            rows = {r.filename: r for r in await artifacts(db, e)}
            for r in rows.values():
                await db.refresh(r)
            assert {r.case_attachment_id for r in rows.values()} == {a.id for a in atts}
            await db.refresh(foreign)
            assert foreign.case_attachment_id is None
            # idempotent: a second promotion copies nothing more
            assert await email_intake.copy_email_evidence(alert=alert, case_id=case.id, user_id=None) == 0
    run(body())


def test_promote_endpoint_copies_email_evidence(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import httpx
    from app.api import deps
    from app.main import app
    raw = make_mail(attachments=[("a.txt", b"hello", "text/plain")])

    async def body():
        async with env(monkeypatch, tmp_path, fallback=True, messages=[raw]) as (e, db, mb):
            try:
                await email_intake.poll_mailbox(db, mb)
                alert_id = (await db.execute(select(Alert.id).where(Alert.tenant_id == e.tid))).scalar()
                case = Case(title="c", tenant_id=e.tid, description="d")
                db.add(case)
                await db.flush()
                e.cases.append(int(case.id))
                await db.commit()
                app.dependency_overrides[deps.require_analyst_or_above] = lambda: SimpleNamespace(id=None)
                app.dependency_overrides[deps.get_effective_tenant_id] = lambda: e.tid
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                    r = await c.post(f"/api/v1/alerts/{alert_id}/promote/{case.id}")
                assert r.status_code == 200, r.text
                n = (await db.execute(select(func.count()).select_from(CaseAttachment).where(
                    CaseAttachment.case_id == case.id))).scalar()
                assert n == 2
                assert all(a.case_attachment_id for a in await artifacts(db, e))
            finally:
                app.dependency_overrides.clear()
    run(body())


def _nctx(db, tid, ctx=None, alert_id=None):
    from types import SimpleNamespace

    from app.workflows.nodes import NodeContext
    return NodeContext(db=db, run=SimpleNamespace(id=-1, tenant_id=tid, depth=0, case_id=None, alert_id=alert_id,
                                                  trigger_payload=(ctx or {}).get("trigger")),
                       step=None, node={}, ctx=ctx or {})


def test_alert_create_node_holds_email_artifacts_past_cleanup(monkeypatch, tmp_path):
    """The stock email-to-alert template: trigger -> alert_create with payload {{ trigger }}."""
    from app.workflows.nodes import alerts as alerts_node
    raw = make_mail(attachments=[("a.txt", b"hello", "text/plain")])

    async def body():
        async with env(monkeypatch, tmp_path, messages=[raw]) as (e, db, mb):
            monkeypatch.setattr(alerts_node, "emit_event", lambda *a, **k: None)
            await add_workflow(db, e)
            await email_intake.poll_mailbox(db, mb)
            run_ = (await db.execute(select(WorkflowRun).where(WorkflowRun.tenant_id == e.tid))).scalars().one()
            trig = run_.trigger_payload
            assert {a.alert_id for a in await artifacts(db, e)} == {None}

            nctx = _nctx(db, e.tid, {"trigger": trig})
            out = await alerts_node.run_alert_create(nctx, {
                "source": "email", "external_id": trig["email"]["message_id"], "title": trig["email"]["subject"],
                "severity": "medium", "payload": "trigger"})
            await db.commit()
            assert out["created"] is True
            arts = await artifacts(db, e)
            assert len(arts) == 2 and {a.alert_id for a in arts} == {out["alert_id"]}

            # an id naming another tenant's artifact is never claimed
            other = Tenant(name="email-test", slug=f"email-{e.h}-o")
            db.add(other)
            await db.flush()
            e.tenants.append(int(other.id))
            foreign = EmailArtifact(tenant_id=other.id, kind="eml", filename="f.eml", size_bytes=1, sha256="0" * 64,
                                    storage_key=f"tenants/{other.id}/email/{secrets.token_hex(8)}")
            db.add(foreign)
            await db.commit()
            out2 = await alerts_node.run_alert_create(nctx, {
                "source": "email", "external_id": "other", "title": "t", "payload": {"eml_artifact_id": int(foreign.id)}})
            await db.commit()
            await db.refresh(foreign)
            assert out2["created"] is True and foreign.alert_id is None

            # 31 days on, the cleanup keeps the alert's evidence
            old = datetime.now(timezone.utc) - timedelta(days=40)
            await db.execute(update(EmailArtifact).where(EmailArtifact.tenant_id == e.tid).values(created_at=old))
            await db.commit()
            res = await email_intake.cleanup_email_artifacts(tenant_ids=[e.tid])
            assert res["artifacts"] == 0 and len(await artifacts(db, e)) == 2
    run(body())


def test_alert_promote_node_copies_email_evidence(monkeypatch, tmp_path):
    import app.tasks.email_intake as tasks
    from app.workflows.nodes import alerts as alerts_node
    raw = make_mail(attachments=[("a.txt", b"hello", "text/plain")])

    async def body():
        async with env(monkeypatch, tmp_path, fallback=True, messages=[raw]) as (e, db, mb):
            queued = []
            monkeypatch.setattr(tasks.copy_email_evidence_task, "delay", lambda *a: queued.append(a))
            monkeypatch.setattr(alerts_node, "emit_event", lambda *a, **k: None)
            import app.tasks.triage as triage
            monkeypatch.setattr(triage.run_case_triage_task, "delay", lambda *a: None)
            await email_intake.poll_mailbox(db, mb)
            alert_id = (await db.execute(select(Alert.id).where(Alert.tenant_id == e.tid))).scalar()

            nctx = _nctx(db, e.tid, alert_id=alert_id)
            out = await alerts_node.run_alert_promote(nctx, {"mode": "new", "title": "from email"})
            await db.commit()
            e.cases.append(int(out["case_id"]))
            assert len(nctx.after_commit) >= 1
            for fn in nctx.after_commit:
                fn()
            assert queued == [(alert_id, out["case_id"])]
            # the Celery task body
            assert await email_intake.copy_evidence_for_alert(alert_id, out["case_id"]) == 2
            atts = (await db.execute(select(CaseAttachment).where(CaseAttachment.case_id == out["case_id"]))).scalars().all()
            assert sorted(a.filename for a in atts) == ["Suspicious invoice.eml", "a.txt"]
            rows = await artifacts(db, e)
            for r in rows:
                await db.refresh(r)
            assert {r.case_attachment_id for r in rows} == {a.id for a in atts}
    run(body())


def test_copy_task_registered():
    from app.worker import celery_app
    assert "app.tasks.email_intake.copy_email_evidence_task" in celery_app.tasks


def test_sweep_and_mailbox_deadlines(monkeypatch, tmp_path):
    from types import SimpleNamespace
    msgs = [make_mail(mid=f"<d{i}@example.test>") for i in range(5)]

    async def body():
        async with env(monkeypatch, tmp_path, messages=msgs) as (e, db, mb):
            # sweep budget already used up: no mailbox is claimed, so it stays due for the next cycle
            assert await email_intake.poll_due_mailboxes(mailbox_ids=[e.mid], budget=0) == []
            row = (await db.execute(select(TenantMailbox).where(TenantMailbox.id == e.mid))).scalars().one()
            await db.refresh(row)
            assert row.last_polled_at is None and e.fake.log == []

            # a mailbox's loop stops between messages at its deadline; the rest stay unseen and untouched
            clock = [0.0]
            monkeypatch.setattr(email_intake, "time", SimpleNamespace(monotonic=lambda: clock[0]))
            real_uid = e.fake.uid

            def ticking_uid(cmd, *a):
                if cmd.upper() == "FETCH" and "PEEK" in a[1]:  # the body fetch, not the RFC822.SIZE check
                    clock[0] += 10
                return real_uid(cmd, *a)
            e.fake.uid = ticking_uid
            assert await email_intake.poll_mailbox(db, mb, deadline=25) == 3
            assert e.fake.fetched == ["1", "2", "3"] and e.fake.deleted == ["1", "2", "3"]
            assert len(await seen_ids(db, e)) == 3
            # next cycle picks the remaining two up
            clock[0] = 0
            assert await email_intake.poll_mailbox(db, mb, deadline=1000) == 2
    run(body())


def test_soft_time_limit_is_not_swallowed(monkeypatch, tmp_path):
    import pytest
    from celery.exceptions import SoftTimeLimitExceeded

    async def body():
        async with env(monkeypatch, tmp_path, messages=[make_mail()]) as (e, db, mb):
            async def slow(*a, **k):
                raise SoftTimeLimitExceeded()
            monkeypatch.setattr(email_intake, "_process_message", slow)
            with pytest.raises(SoftTimeLimitExceeded):
                await email_intake.poll_mailbox(db, mb)
            with pytest.raises(SoftTimeLimitExceeded):
                await email_intake.poll_due_mailboxes(mailbox_ids=[e.mid])
            assert e.fake.deleted == []  # never archived
    run(body())


def test_payload_fields_are_capped(monkeypatch):
    many = ", ".join(f"u{i}@example.test" for i in range(300))
    p = email_intake.parse_email(make_mail(subject="s" * 50_000, to=many + ", " + "x" * 400 + "@example.test"))
    m = p.email
    assert len(m["subject"].encode()) <= email_intake.SUBJECT_MAX_BYTES
    assert len(m["to"]) == email_intake.TO_MAX_ENTRIES and all(len(a) <= 254 for a in m["to"])
    assert len(m["to_joined"].encode()) <= email_intake.TO_JOINED_MAX_BYTES
    monkeypatch.setattr(email_intake.settings, "MAX_UPLOAD_MB", 1)
    atts = [(f"f{i}.bin", b"x" * (1024 * 1024 + 1), "application/octet-stream") for i in range(25)]
    m = email_intake.parse_email(make_mail(attachments=atts)).email
    assert m["attachment_count"] == 25 and len(m["skipped_attachments"]) == email_intake.SKIPPED_MAX_ENTRIES


def test_fallback_payload_is_kept_under_the_alert_size_guard(monkeypatch, tmp_path):
    import json
    raw = make_mail(body="word " * 20_000)  # ~100KB body

    async def body():
        async with env(monkeypatch, tmp_path, fallback=True, messages=[raw]) as (e, db, mb):
            monkeypatch.setattr(email_intake, "MAX_ALERT_PAYLOAD", 30_000)
            monkeypatch.setattr(email_intake, "_fit_payload", lambda p, limit=30_000, _f=email_intake._fit_payload: _f(p, limit))
            await email_intake.poll_mailbox(db, mb)
            alert = (await db.execute(select(Alert).where(Alert.tenant_id == e.tid))).scalars().one()
            assert len(json.dumps(alert.payload)) <= 30_000
            assert alert.payload["email"]["truncated"] is True and alert.payload["email"]["body_text"].startswith("word")
            assert alert.payload["eml_artifact_id"]  # evidence still stored whole
            eml = next(a for a in await artifacts(db, e) if a.kind == "eml")
            assert (tmp_path / eml.storage_key).read_bytes() == raw
    run(body())


def test_cleanup_works_through_more_than_one_batch(monkeypatch, tmp_path):
    async def body():
        async with env(monkeypatch, tmp_path) as (e, db, mb):
            monkeypatch.setattr(email_intake, "CLEANUP_BATCH", 2)
            now = datetime.now(timezone.utc)
            rows = []
            for i in range(5):
                key = f"tenants/{e.tid}/email/{secrets.token_hex(8)}"
                (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / key).write_bytes(b"d")
                rows.append(EmailArtifact(tenant_id=e.tid, kind="eml", filename=f"{i}.eml", size_bytes=1,
                                          sha256="0" * 64, storage_key=key, created_at=now - timedelta(days=40)))
            db.add_all(rows)
            await db.commit()
            res = await email_intake.cleanup_email_artifacts(now, tenant_ids=[e.tid])
            assert res["artifacts"] == 5 and await artifacts(db, e) == []
            assert not [p for p in tmp_path.rglob("*") if p.is_file()]
    run(body())


def test_cleanup_removes_only_unreferenced_old_artifacts_and_old_seen(monkeypatch, tmp_path):
    async def body():
        async with env(monkeypatch, tmp_path) as (e, db, mb):
            now = datetime.now(timezone.utc)
            old, fresh = now - timedelta(days=31), now - timedelta(days=5)
            alert = Alert(tenant_id=e.tid, source="email", external_id="x", title="t", status="pending")
            db.add(alert)
            await db.flush()

            def art(name, created, **kw):
                key = f"tenants/{e.tid}/email/{secrets.token_hex(8)}"
                p = tmp_path / key
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"data")
                return EmailArtifact(tenant_id=e.tid, mailbox_id=e.mid, kind="eml", filename=name, size_bytes=4,
                                     sha256="0" * 64, storage_key=key, created_at=created, **kw)
            rows = {n: art(n, c, **kw) for n, c, kw in [
                ("old-orphan", old, {}), ("fresh-orphan", fresh, {}), ("old-alerted", old, {"alert_id": alert.id})]}
            db.add_all(rows.values())
            db.add_all([MailboxSeen(mailbox_id=e.mid, message_id="old", processed_at=now - timedelta(days=91)),
                        MailboxSeen(mailbox_id=e.mid, message_id="new", processed_at=now - timedelta(days=10))])
            await db.commit()
            keys = {n: r.storage_key for n, r in rows.items()}

            res = await email_intake.cleanup_email_artifacts(now, tenant_ids=[e.tid])
            assert res == {"artifacts": 1, "seen": 1}
            left = {a.filename for a in await artifacts(db, e)}
            assert left == {"fresh-orphan", "old-alerted"}
            assert not (tmp_path / keys["old-orphan"]).exists()
            assert (tmp_path / keys["fresh-orphan"]).exists() and (tmp_path / keys["old-alerted"]).exists()
            assert await seen_ids(db, e) == {"new"}
    run(body())


def test_beat_schedule_and_tasks_registered():
    from app.worker import celery_app
    sched = celery_app.conf.beat_schedule
    assert sched["poll-mailboxes"] == {"task": "app.tasks.email_intake.poll_mailboxes_task", "schedule": 60.0}
    assert sched["cleanup-email-artifacts"] == {"task": "app.tasks.email_intake.cleanup_email_artifacts_task",
                                                "schedule": 86400.0}
    assert "app.tasks.email_intake.poll_mailboxes_task" in celery_app.tasks
    assert "app.tasks.email_intake.cleanup_email_artifacts_task" in celery_app.tasks


def _nested(depth: int, leaf: bytes) -> bytes:
    s = b"MIME-Version: 1.0\r\nMessage-ID: <deep@example.test>\r\n"
    for i in range(depth):
        s += b'Content-Type: multipart/mixed; boundary="b%d"\r\n\r\n--b%d\r\n' % (i, i)
    s += leaf
    for i in reversed(range(depth)):
        s += b"--b%d--\r\n" % i
    return s


def test_parse_email_caps_mime_depth_and_parts():
    """N5: hostile MIME nesting is capped (no RecursionError, no unbounded walk); the rest still parses."""
    leaf = (b'Content-Type: application/pdf; name="x.pdf"\r\nContent-Disposition: attachment; filename="x.pdf"\r\n'
            b"\r\n%PDF-1.4 deep\r\n")
    p = email_intake.parse_email(_nested(email_intake.MIME_MAX_DEPTH + 30, leaf))
    assert p.attachments == [] and any("nested" in s["reason"] for s in p.email["skipped_attachments"])
    wide = EmailMessage()
    wide["Message-ID"] = "<wide@example.test>"
    wide.set_content("x")
    for i in range(email_intake.MIME_MAX_PARTS + 50):
        wide.add_attachment(b"a", maintype="application", subtype="octet-stream", filename=f"{i}.bin")
    p = email_intake.parse_email(wide.as_bytes())
    assert len(p.attachments) < email_intake.MIME_MAX_PARTS
    assert any("too many" in s["reason"] for s in p.email["skipped_attachments"])
    # an attached email nested so deep that re-serialising it recurses is skipped, not fatal
    inner = _nested(400, b"Content-Type: text/plain\r\n\r\nhi\r\n")
    outer = (b"MIME-Version: 1.0\r\nMessage-ID: <outer@example.test>\r\n"
             b'Content-Type: multipart/mixed; boundary="o"\r\n\r\n--o\r\nContent-Type: text/plain\r\n\r\nbody\r\n'
             b"--o\r\nContent-Type: message/rfc822\r\n\r\n" + inner + b"--o--\r\n")
    p = email_intake.parse_email(outer)
    assert p.email["message_id"] == "<outer@example.test>"


def test_poison_message_moves_to_failed_folder_after_three_attempts(monkeypatch, tmp_path):
    """N5: a message that always fails is retried MAX_MESSAGE_FAILURES times, then moved out of the inbox."""
    from app.models.tenant_mailbox import MailboxFailure
    good, bad = make_mail(mid="<good@example.test>"), make_mail(mid="<poison@example.test>")
    real = email_intake.parse_email

    def parse(raw):
        if raw == bad:
            raise RecursionError("maximum recursion depth exceeded")
        return real(raw)
    monkeypatch.setattr(email_intake, "parse_email", parse)

    async def body():
        import pytest
        async with env(monkeypatch, tmp_path, messages=[bad, good]) as (e, db, mb):
            for attempt in range(1, email_intake.MAX_MESSAGE_FAILURES):
                with pytest.raises(email_intake.MailboxError, match="could not be processed"):
                    await email_intake.poll_mailbox(db, mb)
                assert e.fake.copied[-1:] != [("1", "Failed")]
            with pytest.raises(email_intake.MailboxError, match="moved to the Failed folder"):
                await email_intake.poll_mailbox(db, mb)
            f = e.fake
            assert ("1", "Failed") in f.copied and "1" in f.seen and "1" in f.deleted
            assert ("2", "Processed") in f.copied and await seen_ids(db, e) == {"<good@example.test>"}
            fetched = len(f.fetched)
            assert await email_intake.poll_mailbox(db, mb) == 0 and len(f.fetched) == fetched  # no longer unseen
            left = (await db.execute(select(MailboxFailure).where(MailboxFailure.mailbox_id == e.mid))).scalars().all()
            assert left == []
    run(body())


def test_oversize_message_is_not_fetched_and_moves_to_failed(monkeypatch, tmp_path):
    """N10: RFC822.SIZE above the cap: the body is never fetched; the message leaves the unseen batch."""
    big, small = make_mail(mid="<big@example.test>", body="x" * 5000), make_mail(mid="<small@example.test>")
    monkeypatch.setattr(email_intake, "_max_message_bytes", lambda: 2000)

    async def body():
        import pytest
        async with env(monkeypatch, tmp_path, messages=[big, small]) as (e, db, mb):
            with pytest.raises(email_intake.MailboxError, match="moved to the Failed folder"):
                await email_intake.poll_mailbox(db, mb)
            f = e.fake
            assert f.fetched == ["2"] and ("1", "Failed") in f.copied and "1" in f.seen
            assert await seen_ids(db, e) == {"<small@example.test>"}
    run(body())


def test_fetch_wall_clock_deadline(monkeypatch, tmp_path):
    """N10: a server that drips a message gets FETCH_DEADLINE_SECONDS, then the poll stops and the UID is counted."""
    import time as _time
    from app.models.tenant_mailbox import MailboxFailure
    monkeypatch.setattr(email_intake, "FETCH_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(email_intake, "_imap_fetch", lambda conn, uid: _time.sleep(1) or b"")

    async def body():
        import pytest
        async with env(monkeypatch, tmp_path, messages=[make_mail()]) as (e, db, mb):
            t0 = _time.monotonic()
            with pytest.raises(email_intake.MailboxError, match="timed out"):
                await email_intake.poll_mailbox(db, mb)
            assert _time.monotonic() - t0 < 1
            row = (await db.execute(select(MailboxFailure).where(MailboxFailure.mailbox_id == e.mid))).scalars().one()
            assert (row.uid, row.failures) == ("1", 1)
    run(body())


def test_foreign_alert_payload_cannot_claim_email_evidence(monkeypatch, tmp_path):
    """N11: artifact ids in a webhook payload, or in an alert_create run not triggered by that email, claim nothing."""
    from app.workflows.nodes import alerts as alerts_node
    raw = make_mail(attachments=[("a.txt", b"hello", "text/plain")])

    async def body():
        async with env(monkeypatch, tmp_path, messages=[raw]) as (e, db, mb):
            monkeypatch.setattr(alerts_node, "emit_event", lambda *a, **k: None)
            await add_workflow(db, e)
            await email_intake.poll_mailbox(db, mb)  # evidence stored, unclaimed (the run hasn't alerted yet)
            trig = (await db.execute(select(WorkflowRun).where(WorkflowRun.tenant_id == e.tid))).scalars().one().trigger_payload
            ids = email_intake.evidence_ids(trig)
            assert len(ids) == 2
            forged = {**trig}  # a webhook replaying the email payload shape
            alert = Alert(source="email", external_id="forged", title="t", payload=forged, status="pending",
                          severity="medium", tenant_id=e.tid)
            case = Case(title="c", tenant_id=e.tid, description="d")
            db.add_all([alert, case])
            await db.flush()
            e.cases.append(int(case.id))
            await db.commit()
            assert await email_intake.copy_email_evidence(alert=alert, case_id=case.id, user_id=None) == 0
            # alert_create in a run triggered by something else: ids in its payload are ignored
            nctx = _nctx(db, e.tid, {"trigger": {"event": "alert.ingested", "eml_artifact_id": ids[0]}})
            await alerts_node.run_alert_create(nctx, {"source": "email", "external_id": "x2", "title": "t",
                                                      "payload": {"eml_artifact_id": ids[0]}})
            await db.commit()
            assert {a.alert_id for a in await artifacts(db, e)} == {None}
    run(body())
