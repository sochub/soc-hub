"""Symmetric encryption for secrets stored at rest (Slack tokens, tenant secrets, SSO/AI/enrichment
credentials, MFA seeds).

Key separation: the encryption key is never SECRET_KEY itself (which signs JWTs). It is derived with
HKDF-SHA256 under its own label from ENCRYPTION_KEY when that is set, otherwise from SECRET_KEY.

Rotation: decryption tries, in order, the current key, the SECRET_KEY-derived key (when ENCRYPTION_KEY is
set, so data written before it was configured keeps working), every ENCRYPTION_KEYS_PREVIOUS entry and
the legacy key (SHA-256 of SECRET_KEY, used before key separation). Encryption always uses the current
key. `python -m app.scripts.reencrypt_secrets` re-encrypts stored values with the current key.
"""
import base64
import functools
import hashlib

from cryptography.fernet import Fernet, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

_INFO = b"sochub/secret-encryption/v1"


def _derive(material: str) -> bytes:
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(material.encode())
    return base64.urlsafe_b64encode(raw)


def _legacy(secret_key: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest())


def _previous() -> tuple:
    return tuple(k.strip() for k in (settings.ENCRYPTION_KEYS_PREVIOUS or "").split(",") if k.strip())


@functools.lru_cache(maxsize=8)
def _keys(secret_key: str, encryption_key: str, previous: tuple) -> tuple:
    keys = [_derive(encryption_key or secret_key)]
    if encryption_key:
        keys.append(_derive(secret_key))
    keys += [_derive(p) for p in previous]
    keys.append(_legacy(secret_key))
    return tuple(dict.fromkeys(keys))  # dedupe, keep order


def _current_keys() -> tuple:
    return _keys(settings.SECRET_KEY, settings.ENCRYPTION_KEY or "", _previous())


def current_key() -> bytes:
    """The Fernet key new ciphertexts are written with."""
    return _current_keys()[0]


@functools.lru_cache(maxsize=8)
def _multi(keys: tuple) -> MultiFernet:
    return MultiFernet([Fernet(k) for k in keys])


def _fernet() -> MultiFernet:
    return _multi(_current_keys())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def is_current(token: str) -> bool:
    """True if `token` is already encrypted with the current key."""
    try:
        Fernet(current_key()).decrypt(token.encode())
        return True
    except Exception:
        return False


def rotate(token: str) -> str:
    """Re-encrypt a ciphertext under the current key (raises InvalidToken if no known key opens it)."""
    return _fernet().rotate(token.encode()).decode()
