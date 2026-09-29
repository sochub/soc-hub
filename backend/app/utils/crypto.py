"""Symmetric encryption for secrets stored at rest (e.g. Slack tokens).

The Fernet key is derived from SECRET_KEY, so rotating SECRET_KEY makes
previously stored secrets unreadable — admins must re-enter them.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
