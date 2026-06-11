"""Centralised password-strength policy.

Used by every code path that sets a password: user creation, invitation
acceptance, self-service profile updates, and the super-admin bootstrap script.
Keeping the rules in one place means the public `/invitations/accept` endpoint
and the CLI script can never drift out of sync.
"""

import re

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128  # argon2 handles long inputs, but cap to avoid abuse

# A small deny-list of obviously weak choices. This is not a substitute for a
# breach-corpus check, but it cheaply blocks the worst offenders.
_COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwertyuiop", "letmein", "changeme", "admin123",
    "welcome1", "iloveyou", "passw0rd", "adminadmin",
}

_UPPER = re.compile(r"[A-Z]")
_LOWER = re.compile(r"[a-z]")
_DIGIT = re.compile(r"[0-9]")


def validate_password_strength(password: str) -> str:
    """Validate a plaintext password against the policy.

    Returns the password unchanged when valid, raises ``ValueError`` otherwise
    so it can be used directly as a Pydantic validator or in plain code.
    """
    if not isinstance(password, str):
        raise ValueError("Password must be a string.")

    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters long."
        )
    if not _UPPER.search(password):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not _LOWER.search(password):
        raise ValueError("Password must contain at least one lowercase letter.")
    if not _DIGIT.search(password):
        raise ValueError("Password must contain at least one digit.")
    if password.lower() in _COMMON_PASSWORDS:
        raise ValueError("Password is too common. Choose a less predictable password.")

    return password
