"""AI-layer errors and secret scrubbing for provider error messages."""
import re
from typing import Iterable, Optional

_MAX = 300
_PATTERNS = [
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{16,}"), "sk-***"),
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "AKIA***"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "AIza***"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-~+/=]{8,}"), "Bearer ***"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "<private key>"),
    (re.compile(r"ya29\.[\w\-.]+"), "***"),
    (re.compile(r"\b1//[\w\-]{20,}"), "***"),
    (re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}"), "Basic ***"),
    (re.compile(
        r'(?i)\b(x-api-key|api[_-]?key|key|aws_secret_access_key|aws_session_token|'
        r'secret[_-]?(?:access[_-]?)?key|x-amz-security-token|security[_-]?token|'
        r'access[_-]?token|token|password)(\s*[=:]\s*|":\s*")([^\s"&,;]+)'
    ), r"\1\2***"),
    (re.compile(r'"private_key"\s*:\s*"[^"]*"'), '"private_key": "***"'),
]


class AIError(Exception):
    """A provider call failed for a reason the caller may show to the user."""


class AIUnavailable(AIError):
    """Auth, network, quota, timeout, misconfiguration — AI is unusable right now."""


def scrub(text: str, secrets: Iterable[Optional[str]] = ()) -> str:
    out = str(text)
    for s in sorted((x for x in secrets if x), key=len, reverse=True):
        if len(s) >= 4:
            out = out.replace(s, "***")
    for rx, repl in _PATTERNS:
        out = rx.sub(repl, out)
    return out[:_MAX]
