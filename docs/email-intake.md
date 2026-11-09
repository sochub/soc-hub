# Email intake

A tenant admin connects an IMAP mailbox. New mail in its folder fires the `email.received` workflow trigger, so
reported phishing and similar mail becomes an alert (or anything else a workflow can do).

## Mailbox setup

**Settings -> Integrations -> Mailboxes** (admins only). Fields:

| Field | Notes |
|---|---|
| name, host, username, password | Required. The password is write-only and stored encrypted; leave it blank on edit to keep it |
| port | Default 993 |
| folder | Default `INBOX` |
| poll interval | Seconds, minimum 60 (default 120) |
| Use SSL | On: implicit TLS. Off: plain connect then **STARTTLS** |
| Enabled | Disabled mailboxes are not polled |
| Create an alert if no workflow handles the email | Fallback alert, see below |

Use **Test** to connect, log in and count the messages in the folder. Errors are deliberately generic
(`authentication failed`, `TLS handshake failed`, `folder not found`).

**TLS is mandatory.** Certificates are verified. With "Use SSL" off the server must advertise STARTTLS, otherwise
the connection is refused; login never happens over cleartext.

**Private addresses.** Hosts that resolve to a non-public address (private, loopback, link-local, CGNAT and other
non-global ranges) are rejected. For an internal mail server the platform operator lists the exact hostname in
the `MAILBOX_PRIVATE_HOSTS` environment setting (a JSON list, for example `["mail.corp.internal"]`).

## The `email.received` trigger

Workflows with trigger `email.received` run once for each new email, for any enabled mailbox of the
tenant. The trigger filter is evaluated per message against `email`:

| Field | Meaning |
|---|---|
| `email.sender` | Lower-cased From address |
| `email.to`, `email.to_joined` | Recipient addresses from To, Cc, Delivered-To and X-Original-To (list; joined string) |
| `email.subject`, `email.body_text` | Subject; plain-text body (HTML is converted to text, links kept), capped at 64 KB |
| `email.date`, `email.message_id` | Date header (ISO) and Message-ID |
| `email.has_attachments`, `email.attachment_count` | Attachment info |
| `email.skipped_attachments` | Attachments over the upload limit, name and size only |

Example filter, matching a plus-addressed reporting alias: `'soc+phishing' in email.to_joined`. An empty filter
matches every message. A filter that errors counts as no match.

## "Email to alert" template

Install the stock **Email to alert** template (Workflows -> templates). It creates a pending alert per email:
source `email`, the Message-ID as external id, the subject as title, severity medium, full payload. Narrow it with
the trigger filter, for example `email.has_attachments`.

## Fallback alert

If a mailbox has "Create an alert if no workflow handles the email" on, an email that matches **no** enabled
`email.received` workflow still becomes a pending alert (subject as title, severity medium). Suppression rules
apply as for any alert. With it off, unmatched mail is only marked as seen.

## Processed folder

After a message is handled, the poller marks it seen and copies it to a `Processed` folder (created on first
use), then removes it from the inbox. If the copy fails the message stays in the inbox, marked seen. Mail is
never deleted outright. Only **UNSEEN** messages are fetched, so mail you mark read by hand is skipped. A message
that fails to process stays unseen and is retried next cycle. Re-delivered mail (same Message-ID) is ignored.

Each mailbox handles at most **50 messages per cycle**; the rest wait for the next one.

## Evidence retention

- The original `.eml` and its attachments (within the upload size limit) are stored **only when something
  consumes the email**: a workflow matched, or the fallback alert was created. Otherwise only a dedupe record is
  kept.
- Stored files not referenced by an alert or case are deleted after **30 days**; dedupe records after 90 days.
- When an alert from email is **promoted to a case**, the `.eml` and attachments are copied into the case as
  regular attachments ("Original email (from email intake)"), and then live as long as the case. This also
  applies when a workflow promotes the alert.
