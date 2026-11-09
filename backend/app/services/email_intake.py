"""IMAP email intake. Task 4.3 extends this module with polling; for now only the connection test."""
import imaplib

from app.utils.crypto import decrypt


def test_connection(mailbox) -> dict:
    """Connect, log in, select the folder read-only and count messages. Never leaks the password in `error`."""
    password = ""
    try:
        password = decrypt(mailbox.password_enc)
        cls = imaplib.IMAP4_SSL if mailbox.use_ssl else imaplib.IMAP4
        conn = cls(mailbox.host, mailbox.port, timeout=10)
        try:
            conn.login(mailbox.username, password)
            typ, data = conn.select(mailbox.folder, readonly=True)
            if typ != "OK":
                return {"ok": False, "message_count": None, "error": f"Cannot select folder {mailbox.folder!r}"}
            return {"ok": True, "message_count": int(data[0]), "error": None}
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        if password:
            msg = msg.replace(password, "***")
        return {"ok": False, "message_count": None, "error": msg[:500]}
