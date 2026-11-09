"""IMAP email intake. Task 4.3 extends this module with polling; for now the host check and connection test."""
import imaplib
import ipaddress
import socket
import ssl
from typing import Optional

from app.core.config import settings
from app.utils.crypto import decrypt

PRIVATE_HOST_MSG = "host resolves to a private address; add it to MAILBOX_PRIVATE_HOSTS to allow"


def validate_mailbox_host(host: str) -> Optional[str]:
    """Return an error message if `host` resolves to a non-public address (SSRF guard), else None.

    Exact hosts in settings.MAILBOX_PRIVATE_HOSTS are allowed. Note: we later connect by hostname (with
    verified TLS), so a DNS-rebinding race between this check and the connect is an accepted trade-off.
    """
    if host in settings.MAILBOX_PRIVATE_HOSTS:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return "host could not be resolved"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return "host could not be resolved"
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified):
            return PRIVATE_HOST_MSG
    return None


def _fail(msg: str) -> dict:
    return {"ok": False, "message_count": None, "error": msg}


def test_connection(mailbox) -> dict:
    """Connect, log in over TLS, select the folder read-only and count messages.

    Errors are generic strings only (no exception text or server banners), so nothing internal or secret leaks.
    """
    if (err := validate_mailbox_host(mailbox.host)):
        return _fail(err)
    password = ""
    conn = None
    try:
        password = decrypt(mailbox.password_enc)
        ctx = ssl.create_default_context()
        if mailbox.use_ssl:
            conn = imaplib.IMAP4_SSL(mailbox.host, mailbox.port, ssl_context=ctx, timeout=10)
        else:
            conn = imaplib.IMAP4(mailbox.host, mailbox.port, timeout=10)
            if "STARTTLS" not in getattr(conn, "capabilities", ()):
                return _fail("server does not support STARTTLS")
            conn.starttls(ctx)  # never log in over cleartext
        conn.login(mailbox.username, password)
        typ, data = conn.select(mailbox.folder, readonly=True)
        if typ != "OK":
            return _fail("folder not found")
        return {"ok": True, "message_count": int(data[0]), "error": None}
    except ssl.SSLError:
        return _fail("TLS handshake failed")
    except imaplib.IMAP4.error:
        return _fail("authentication failed" if conn is not None and getattr(conn, "state", "") == "NONAUTH"
                     else "IMAP error")
    except Exception:
        return _fail("connection failed")
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass
