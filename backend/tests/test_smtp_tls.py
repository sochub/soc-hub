"""N14: outbound SMTP verifies TLS and never logs in or sends in clear text unless SMTP_ALLOW_PLAINTEXT is set."""
import smtplib
import ssl

from app.services import email_service


class _SMTP:
    tls, calls = True, []

    def __init__(self, host, port, context=None, timeout=None):
        _SMTP.calls.append(("connect", type(self).__name__, port, context))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return _SMTP.tls and name == "starttls"

    def starttls(self, context=None):
        _SMTP.calls.append(("starttls", context))

    def login(self, *a):
        _SMTP.calls.append(("login",))

    def send_message(self, msg):
        _SMTP.calls.append(("send",))


class _SMTPS(_SMTP):
    pass


def _setup(monkeypatch, port=587, tls=True, plaintext=False):
    _SMTP.tls, _SMTP.calls = tls, []
    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _SMTPS)
    s = email_service.settings
    for k, v in {"SMTP_HOST": "smtp.example.test", "SMTP_FROM_EMAIL": "f@example.test", "SMTP_PORT": port,
                 "SMTP_USER": "u", "SMTP_PASSWORD": "p", "SMTP_ALLOW_PLAINTEXT": plaintext}.items():
        monkeypatch.setattr(s, k, v)


def test_starttls_uses_verified_context(monkeypatch):
    for port in (587, 25):
        _setup(monkeypatch, port=port)
        assert email_service._send_email("x@example.test", "s", "b", log_recipient=False)
        ctx = next(c[1] for c in _SMTP.calls if c[0] == "starttls")
        assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
        assert [c[0] for c in _SMTP.calls] == ["connect", "starttls", "login", "send"]


def test_no_starttls_means_no_login_or_send(monkeypatch):
    _setup(monkeypatch, tls=False)
    assert email_service._send_email("x@example.test", "s", "b", log_recipient=False) is False
    assert [c[0] for c in _SMTP.calls] == ["connect"]
    _setup(monkeypatch, tls=False, plaintext=True)  # explicit dev opt-in
    assert email_service._send_email("x@example.test", "s", "b", log_recipient=False)


def test_port_465_is_implicit_tls(monkeypatch):
    _setup(monkeypatch, port=465)
    assert email_service._send_email("x@example.test", "s", "b", log_recipient=False)
    kind, ctx = _SMTP.calls[0][1], _SMTP.calls[0][3]
    assert kind == "_SMTPS" and ctx.verify_mode == ssl.CERT_REQUIRED
    assert "starttls" not in [c[0] for c in _SMTP.calls]
