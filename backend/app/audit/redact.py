"""The one place that decides what may be stored in an audit row's `changes`."""
import re
from typing import Any, Optional

_SENSITIVE = re.compile(
    r"password|passwd|secret|token|api[_-]?key|key_enc|value_enc|mfa_secret|otp|totp|authorization|cookie|private|credential",
    re.IGNORECASE,
)
MASK = "***"


_IDENT = re.compile(r"[a-z0-9_]{1,64}")


def _is_name_list(key: str, value: Any) -> bool:
    # Metadata lists of field names, e.g. "secrets_updated": ["api_key"], name fields rather than hold values.
    # Only lists of plain identifiers under these suffixes are exempt; anything else under a sensitive key is masked.
    return (
        key.lower().endswith(("_updated", "_fields", "_names"))
        and isinstance(value, list)
        and all(isinstance(v, str) and _IDENT.fullmatch(v) for v in value)
    )


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (MASK if isinstance(k, str) and _SENSITIVE.search(k) and not _is_name_list(k, v) else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value


def email_domain(email: str) -> Optional[str]:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None
