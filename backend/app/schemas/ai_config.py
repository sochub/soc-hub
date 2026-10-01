from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

Provider = Literal["ollama", "openai", "openai_compatible", "anthropic", "gemini", "vertex", "bedrock"]

_SLUG = r"^[a-z0-9-]{1,63}$"  # AWS region / GCP project and location: never a hostname or path


class AIConfigIn(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    api_base: Optional[str] = Field(None, max_length=500)
    region: Optional[str] = Field(None, pattern=_SLUG)
    project: Optional[str] = Field(None, pattern=_SLUG)
    location: Optional[str] = Field(None, pattern=_SLUG)
    auth_mode: Optional[Literal["role", "keys"]] = None
    role_arn: Optional[str] = Field(None, max_length=300)
    # secrets — write-only; blank/omitted keeps the stored value (unless the provider changes)
    api_key: Optional[str] = Field(None, max_length=500)
    organization: Optional[str] = Field(None, max_length=200)
    access_key_id: Optional[str] = Field(None, max_length=200)
    secret_access_key: Optional[str] = Field(None, max_length=500)
    session_token: Optional[str] = Field(None, max_length=4000)
    service_account_json: Optional[str] = Field(None, max_length=20000)

    @field_validator("api_base")
    @classmethod
    def _no_userinfo(cls, v: Optional[str]) -> Optional[str]:
        if v and "@" in urlsplit(v.strip()).netloc:
            raise ValueError("api_base must not contain credentials (user:pass@)")
        return v
