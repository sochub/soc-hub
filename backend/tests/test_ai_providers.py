import json
import pytest

from app.ai.providers import ProviderConfig, ConfigError, build_kwargs, missing_fields, PROVIDERS, SECRET_FIELDS


def cfg(provider, **kw):
    kw.setdefault("model", "m1")
    kw.setdefault("source", "tenant")
    return ProviderConfig(provider=provider, **kw)


def test_seven_providers():
    assert set(PROVIDERS) == {"ollama", "openai", "openai_compatible", "anthropic", "gemini", "vertex", "bedrock"}
    assert set(SECRET_FIELDS) == set(PROVIDERS)


def test_ollama():
    kw = build_kwargs(cfg("ollama", api_base="http://h:11434"))
    assert kw == {"model": "ollama_chat/m1", "api_base": "http://h:11434"}


def test_openai_with_org():
    kw = build_kwargs(cfg("openai", secrets={"api_key": "k", "organization": "org1"}))
    assert kw == {"model": "openai/m1", "api_key": "k", "organization": "org1"}


def test_openai_compatible_without_key_gets_placeholder():
    kw = build_kwargs(cfg("openai_compatible", api_base="https://llm.example/v1"))
    assert kw["model"] == "openai/m1" and kw["api_base"] == "https://llm.example/v1" and kw["api_key"] == "not-needed"


def test_anthropic_and_gemini():
    assert build_kwargs(cfg("anthropic", secrets={"api_key": "a"})) == {"model": "anthropic/m1", "api_key": "a"}
    assert build_kwargs(cfg("gemini", secrets={"api_key": "g"})) == {"model": "gemini/m1", "api_key": "g"}


def test_vertex_tenant_requires_and_passes_json():
    sa = {"type": "service_account", "client_email": "x@y", "private_key": "k"}
    kw = build_kwargs(cfg("vertex", project="p", location="us-central1", secrets={"service_account_json": json.dumps(sa)}))
    creds = json.loads(kw.pop("vertex_credentials"))
    assert kw == {"model": "vertex_ai/m1", "vertex_project": "p", "vertex_location": "us-central1"}
    assert creds == {**sa, "token_uri": "https://oauth2.googleapis.com/token"}
    assert "secrets.service_account_json" in missing_fields(cfg("vertex", project="p", location="l"))


def test_vertex_deployment_uses_adc():
    kw = build_kwargs(cfg("vertex", source="deployment", project="p", location="l"))
    assert "vertex_credentials" not in kw


def test_bedrock_chain_and_creds():
    assert build_kwargs(cfg("bedrock", region="us-east-1", auth_mode="chain", source="deployment")) == {"model": "bedrock/m1", "aws_region_name": "us-east-1"}
    kw = build_kwargs(cfg("bedrock", region="us-east-1", auth_mode="role", role_arn="arn:aws:iam::1:role/r"),
                      aws_creds={"aws_access_key_id": "A", "aws_secret_access_key": "S", "aws_session_token": "T"})
    assert kw["aws_access_key_id"] == "A" and kw["aws_session_token"] == "T"


def test_bedrock_tenant_mode_requirements():
    assert "role_arn" in missing_fields(cfg("bedrock", region="r", auth_mode="role"))
    m = missing_fields(cfg("bedrock", region="r", auth_mode="keys"))
    assert "secrets.access_key_id" in m and "secrets.secret_access_key" in m
    assert "auth_mode" in missing_fields(cfg("bedrock", region="r", auth_mode="chain"))  # tenants can't use the server chain


@pytest.mark.parametrize("provider,extra", [
    ("ollama", {}), ("openai", {}), ("anthropic", {}), ("gemini", {}), ("openai_compatible", {}),
    ("vertex", {"project": "p"}), ("bedrock", {}),
])
def test_missing_fields_raise(provider, extra):
    with pytest.raises(ConfigError):
        build_kwargs(cfg(provider, **extra))


def test_model_required():
    assert "model" in missing_fields(cfg("openai", model="", secrets={"api_key": "k"}))


def test_unknown_provider():
    with pytest.raises(ConfigError):
        build_kwargs(cfg("azure", secrets={"api_key": "k"}))


@pytest.mark.parametrize("model", ["meta-llama/Llama-3-70b", "us.anthropic.claude-3-5-sonnet-20241022-v2:0", "gpt-4o-mini"])
def test_model_id_passed_verbatim(model):
    kw = build_kwargs(cfg("openai_compatible", model=model, api_base="https://x/v1"))
    assert kw["model"] == f"openai/{model}"
    kw = build_kwargs(cfg("bedrock", model=model, region="us-east-1", auth_mode="chain", source="deployment"))
    assert kw["model"] == f"bedrock/{model}"


def test_invalid_vertex_json_is_config_error():
    with pytest.raises(ConfigError):
        build_kwargs(cfg("vertex", project="p", location="l", secrets={"service_account_json": "{not json"}))


def test_tenant_bedrock_without_resolved_creds_raises():
    # Without aws_creds
    with pytest.raises(ConfigError):
        build_kwargs(cfg("bedrock", region="us-east-1", auth_mode="role", role_arn="arn:aws:iam::1:role/r"))
    # With incomplete aws_creds (missing access_key_id)
    with pytest.raises(ConfigError):
        build_kwargs(cfg("bedrock", region="us-east-1", auth_mode="role", role_arn="arn:aws:iam::1:role/r"),
                    aws_creds={"aws_secret_access_key": "S"})
    # With incomplete aws_creds (missing secret_access_key)
    with pytest.raises(ConfigError):
        build_kwargs(cfg("bedrock", region="us-east-1", auth_mode="role", role_arn="arn:aws:iam::1:role/r"),
                    aws_creds={"aws_access_key_id": "A"})


SA_OK = {"type": "service_account", "project_id": "p", "private_key_id": "kid", "private_key": "-----BEGIN-----",
         "client_email": "svc@p.iam.gserviceaccount.com", "client_id": "1",
         "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
         "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs", "client_x509_cert_url": "https://x"}


def _vertex(sa):
    return cfg("vertex", project="p", location="l", secrets={"service_account_json": json.dumps(sa)})


@pytest.mark.parametrize("sa", [
    {"type": "external_account", "client_email": "x@y", "private_key": "k", "token_url": "https://evil",
     "credential_source": {"file": "/proc/self/environ"}},
    {"type": "authorized_user", "client_email": "x@y", "private_key": "k", "refresh_token": "r"},
    {**SA_OK, "token_uri": "https://evil.example/token"},
    {**SA_OK, "type": None},
    {k: v for k, v in SA_OK.items() if k != "private_key"},
    {**SA_OK, "client_email": ""},
    ["not", "a", "dict"],
])
def test_vertex_rejects_non_service_account_json(sa):
    with pytest.raises(ConfigError):
        build_kwargs(_vertex(sa))


def test_vertex_service_account_normalised():
    sa = {**SA_OK, "universe_domain": "evil.example", "credential_source": {"url": "http://169.254.169.254"}}
    sa.pop("token_uri")
    creds = json.loads(build_kwargs(_vertex(sa))["vertex_credentials"])
    assert creds == SA_OK  # token_uri forced, universe_domain and unknown keys dropped
