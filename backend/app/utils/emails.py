def normalize_email(email: str) -> str:
    """Canonical stored form of an email: trimmed and lower-cased.

    Every write path uses this, and users.email has a UNIQUE index on
    lower(email), so 'Alice@x.com' and 'alice@x.com' are the same account.
    """
    return email.strip().lower()
