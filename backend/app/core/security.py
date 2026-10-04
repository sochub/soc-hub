import secrets
from datetime import datetime, timedelta
from typing import Optional
import jwt
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


MFA_CHALLENGE_MINUTES = 5


def issue_access_token(user, active_tenant_id) -> str:
    return create_access_token({"sub": user.email, "active_tenant_id": active_tenant_id,
                                "tv": user.token_version or 0})


def issue_mfa_challenge(user) -> str:
    """Short-lived, single-use (jti is spent in Redis on success) login challenge."""
    return create_access_token({"sub": user.email, "purpose": "mfa", "tv": user.token_version or 0,
                                "jti": secrets.token_urlsafe(16)},
                               expires_delta=timedelta(minutes=MFA_CHALLENGE_MINUTES))


def decode_mfa_challenge(token: str) -> dict:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    if payload.get("purpose") != "mfa":
        raise jwt.InvalidTokenError("not an mfa challenge")
    return payload


def bump_token_version(user) -> None:
    user.token_version = (user.token_version or 0) + 1
