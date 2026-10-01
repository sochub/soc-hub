from typing import Literal, Optional

from pydantic import BaseModel, Field

Provider = Literal["ollama", "openai", "openai_compatible", "anthropic", "gemini", "vertex", "bedrock"]


class AIConfigIn(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    api_base: Optional[str] = Field(None, max_length=500)
    region: Optional[str] = Field(None, max_length=50)
    project: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=100)
    auth_mode: Optional[Literal["role", "keys"]] = None
    role_arn: Optional[str] = Field(None, max_length=300)
    # secrets — write-only; blank/omitted keeps the stored value (unless the provider changes)
    api_key: Optional[str] = Field(None, max_length=500)
    organization: Optional[str] = Field(None, max_length=200)
    access_key_id: Optional[str] = Field(None, max_length=200)
    secret_access_key: Optional[str] = Field(None, max_length=500)
    session_token: Optional[str] = Field(None, max_length=4000)
    service_account_json: Optional[str] = Field(None, max_length=20000)
