"""Pure provider definitions: which fields each provider needs and the LiteLLM kwargs it gets."""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PROVIDERS = ("ollama", "openai", "openai_compatible", "anthropic", "gemini", "vertex", "bedrock")

_PREFIX = {
    "ollama": "ollama_chat", "openai": "openai", "openai_compatible": "openai",
    "anthropic": "anthropic", "gemini": "gemini", "vertex": "vertex_ai", "bedrock": "bedrock",
}

SECRET_FIELDS: Dict[str, frozenset] = {
    "ollama": frozenset(),
    "openai": frozenset({"api_key", "organization"}),
    "openai_compatible": frozenset({"api_key"}),
    "anthropic": frozenset({"api_key"}),
    "gemini": frozenset({"api_key"}),
    "vertex": frozenset({"service_account_json"}),
    "bedrock": frozenset({"access_key_id", "secret_access_key", "session_token"}),
}
ALL_SECRET_FIELDS = frozenset().union(*SECRET_FIELDS.values())
SSRF_GUARDED = frozenset({"ollama", "openai_compatible"})


class ConfigError(ValueError):
    pass


@dataclass
class ProviderConfig:
    provider: str
    model: str
    source: str  # "tenant" | "deployment"
    tenant_id: Optional[int] = None
    api_base: Optional[str] = None
    region: Optional[str] = None
    project: Optional[str] = None
    location: Optional[str] = None
    auth_mode: Optional[str] = None  # bedrock: "chain" | "role" | "keys"
    role_arn: Optional[str] = None
    external_id: Optional[str] = None
    secrets: dict = field(default_factory=dict)


def missing_fields(cfg: ProviderConfig) -> List[str]:
    p, s = cfg.provider, cfg.secrets or {}
    missing: List[str] = []
    if not cfg.model:
        missing.append("model")
    if p in ("ollama", "openai_compatible") and not cfg.api_base:
        missing.append("api_base")
    if p in ("openai", "anthropic", "gemini") and not s.get("api_key"):
        missing.append("secrets.api_key")
    if p == "vertex":
        if not cfg.project:
            missing.append("project")
        if not cfg.location:
            missing.append("location")
        if cfg.source == "tenant" and not s.get("service_account_json"):
            missing.append("secrets.service_account_json")
    if p == "bedrock":
        if not cfg.region:
            missing.append("region")
        if cfg.source == "tenant":
            if cfg.auth_mode == "role":
                if not cfg.role_arn:
                    missing.append("role_arn")
            elif cfg.auth_mode == "keys":
                if not s.get("access_key_id"):
                    missing.append("secrets.access_key_id")
                if not s.get("secret_access_key"):
                    missing.append("secrets.secret_access_key")
            else:
                missing.append("auth_mode")
    return missing


def build_kwargs(cfg: ProviderConfig, aws_creds: Optional[dict] = None) -> dict:
    if cfg.provider not in PROVIDERS:
        raise ConfigError(f"unknown provider '{cfg.provider}'")
    missing = missing_fields(cfg)
    if missing:
        raise ConfigError("missing " + ", ".join(missing))
    p, s = cfg.provider, cfg.secrets or {}
    kw: dict = {"model": f"{_PREFIX[p]}/{cfg.model}"}
    if p in ("ollama", "openai_compatible"):
        kw["api_base"] = cfg.api_base
    if p in ("openai", "openai_compatible", "anthropic", "gemini") and s.get("api_key"):
        kw["api_key"] = s["api_key"]
    if p == "openai_compatible" and "api_key" not in kw:
        kw["api_key"] = "not-needed"  # LiteLLM's OpenAI client requires some key
    if p == "openai" and s.get("organization"):
        kw["organization"] = s["organization"]
    if p == "vertex":
        kw["vertex_project"] = cfg.project
        kw["vertex_location"] = cfg.location
        sa = s.get("service_account_json")
        if sa:
            try:
                parsed = json.loads(sa)
            except ValueError:
                raise ConfigError("service account JSON is not valid JSON")
            if not isinstance(parsed, dict) or "client_email" not in parsed:
                raise ConfigError("service account JSON must be a Google service-account key")
            kw["vertex_credentials"] = sa
    if p == "bedrock":
        kw["aws_region_name"] = cfg.region
        if aws_creds:
            kw.update({k: v for k, v in aws_creds.items() if v})
    return kw
