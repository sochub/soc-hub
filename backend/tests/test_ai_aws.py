import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.ai import aws
from app.ai.errors import AIUnavailable
from app.ai.providers import ProviderConfig


class FakeSTS:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def assume_role(self, **kw):
        self.calls.append(kw)
        if self.fail:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": self.fail, "Message": "nope"}}, "AssumeRole")
        return {"Credentials": {"AccessKeyId": "ASIAXXX", "SecretAccessKey": "sec", "SessionToken": "tok",
                                "Expiration": datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)}}

    def get_caller_identity(self):
        self.calls.append("gci")
        if self.fail:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": self.fail, "Message": "nope"}}, "GetCallerIdentity")
        return {"Arn": "arn:aws:iam::999:role/server"}


def role_cfg(**kw):
    base = dict(provider="bedrock", model="m", source="tenant", tenant_id=7, region="us-east-1",
                auth_mode="role", role_arn="arn:aws:iam::123:role/r", external_id="ext-1")
    base.update(kw)
    return ProviderConfig(**base)


def run(coro):
    return asyncio.run(coro)


def setup_function():
    aws.clear_cache()


def test_deployment_and_chain_use_no_keys():
    assert run(aws.bedrock_credentials(ProviderConfig("bedrock", "m", "deployment", region="r", auth_mode="chain"))) is None


def test_keys_mode():
    cfg = ProviderConfig("bedrock", "m", "tenant", tenant_id=1, region="r", auth_mode="keys",
                         secrets={"access_key_id": "AKIA1", "secret_access_key": "S1"})
    assert run(aws.bedrock_credentials(cfg)) == {"aws_access_key_id": "AKIA1", "aws_secret_access_key": "S1"}


def test_role_mode_assumes_with_external_id_and_caches():
    sts = FakeSTS()
    now = datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc)
    c1 = run(aws.bedrock_credentials(role_cfg(), now=now, sts_factory=lambda region: sts))
    c2 = run(aws.bedrock_credentials(role_cfg(), now=now + timedelta(minutes=30), sts_factory=lambda region: sts))
    assert c1 == c2 == {"aws_access_key_id": "ASIAXXX", "aws_secret_access_key": "sec", "aws_session_token": "tok"}
    assert len(sts.calls) == 1
    call = sts.calls[0]
    assert call["RoleArn"] == "arn:aws:iam::123:role/r" and call["ExternalId"] == "ext-1"
    assert call["RoleSessionName"] == "sochub-tenant-7" and call["DurationSeconds"] == 3600


def test_role_mode_refreshes_five_minutes_before_expiry():
    sts = FakeSTS()
    near = datetime(2030, 1, 1, 11, 56, tzinfo=timezone.utc)  # 4 min before expiry
    run(aws.bedrock_credentials(role_cfg(), now=near - timedelta(hours=1), sts_factory=lambda r: sts))
    run(aws.bedrock_credentials(role_cfg(), now=near, sts_factory=lambda r: sts))
    assert len(sts.calls) == 2


def test_role_change_is_a_new_cache_key():
    sts = FakeSTS()
    now = datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc)
    run(aws.bedrock_credentials(role_cfg(), now=now, sts_factory=lambda r: sts))
    run(aws.bedrock_credentials(role_cfg(role_arn="arn:aws:iam::123:role/other"), now=now, sts_factory=lambda r: sts))
    assert len(sts.calls) == 2


def test_assume_failure_is_unavailable_with_code():
    with pytest.raises(AIUnavailable, match="AccessDenied"):
        run(aws.bedrock_credentials(role_cfg(), sts_factory=lambda r: FakeSTS(fail="AccessDenied")))


def test_clear_cache_per_tenant():
    sts = FakeSTS()
    now = datetime(2030, 1, 1, 11, 0, tzinfo=timezone.utc)
    run(aws.bedrock_credentials(role_cfg(), now=now, sts_factory=lambda r: sts))
    aws.clear_cache(7)
    run(aws.bedrock_credentials(role_cfg(), now=now, sts_factory=lambda r: sts))
    assert len(sts.calls) == 2


# --- R1: failure logging -------------------------------------------------------

def test_assume_failure_logs_warning_without_secrets(caplog):
    cfg = role_cfg(external_id="ext-SUPERSECRET-42",
                   secrets={"access_key_id": "AKIAABCDEFGHIJKLMNOP", "secret_access_key": "sekrit-value"})
    with caplog.at_level(logging.WARNING, logger="app.ai.aws"):
        with pytest.raises(AIUnavailable):
            run(aws.bedrock_credentials(cfg, sts_factory=lambda r: FakeSTS(fail="AccessDenied")))
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "app.ai.aws"]
    assert warnings
    text = "\n".join(r.getMessage() for r in warnings)
    assert "AccessDenied" in text
    assert "arn:aws:iam::123:role/r" in text
    assert "tenant_id=7" in text
    for secret in ("ext-SUPERSECRET-42", "sekrit-value", "AKIAABCDEFGHIJKLMNOP", "ASIAXXX", "tok"):
        assert secret not in text


def test_principal_failure_logs_warning(caplog, monkeypatch):
    monkeypatch.setattr(aws, "_default_sts", lambda region: FakeSTS(fail="ExpiredToken"))
    with caplog.at_level(logging.WARNING, logger="app.ai.aws"):
        assert run(aws.deployment_principal_arn()) is None
    text = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "ExpiredToken" in text


# --- R2: negative cache and short timeouts -------------------------------------

def test_deployment_principal_arn_caches_success(monkeypatch):
    sts = FakeSTS()
    monkeypatch.setattr(aws, "_default_sts", lambda region: sts)
    assert run(aws.deployment_principal_arn()) == "arn:aws:iam::999:role/server"
    assert run(aws.deployment_principal_arn()) == "arn:aws:iam::999:role/server"
    assert sts.calls == ["gci"]


def test_deployment_principal_arn_negative_cache(monkeypatch):
    sts = FakeSTS(fail="AccessDenied")
    monkeypatch.setattr(aws, "_default_sts", lambda region: sts)
    assert run(aws.deployment_principal_arn()) is None
    assert run(aws.deployment_principal_arn()) is None
    assert sts.calls == ["gci"]


def test_negative_cache_expires_after_ten_minutes(monkeypatch):
    sts = FakeSTS(fail="AccessDenied")
    monkeypatch.setattr(aws, "_default_sts", lambda region: sts)
    t0 = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert run(aws.deployment_principal_arn(now=t0)) is None
    assert run(aws.deployment_principal_arn(now=t0 + timedelta(minutes=9))) is None
    assert len(sts.calls) == 1
    assert run(aws.deployment_principal_arn(now=t0 + timedelta(minutes=11))) is None
    assert len(sts.calls) == 2


def test_clear_cache_resets_principal_caches(monkeypatch):
    failing = FakeSTS(fail="AccessDenied")
    monkeypatch.setattr(aws, "_default_sts", lambda region: failing)
    assert run(aws.deployment_principal_arn()) is None
    aws.clear_cache()
    ok = FakeSTS()
    monkeypatch.setattr(aws, "_default_sts", lambda region: ok)
    assert run(aws.deployment_principal_arn()) == "arn:aws:iam::999:role/server"
    aws.clear_cache()
    ok2 = FakeSTS()
    monkeypatch.setattr(aws, "_default_sts", lambda region: ok2)
    run(aws.deployment_principal_arn())
    assert ok2.calls == ["gci"]


def test_sts_client_uses_short_timeouts(monkeypatch):
    seen = []

    sessions = []

    class FakeSession:
        def __init__(self):
            sessions.append(self)

        def client(self, service, **kw):
            seen.append((service, kw))
            return object()

    def module_client(*a, **kw):
        raise AssertionError("must not use the shared default session")

    monkeypatch.setattr(aws.boto3.session, "Session", FakeSession)
    monkeypatch.setattr(aws.boto3, "client", module_client)
    aws._default_sts("eu-west-1")
    aws._default_sts(None)
    assert [s for s, _ in seen] == ["sts", "sts"]
    assert len(sessions) == 2  # fresh session per client
    assert seen[0][1]["region_name"] == "eu-west-1"
    for _, kw in seen:
        cfg = kw["config"]
        assert cfg.connect_timeout == 2 and cfg.read_timeout == 2
        assert cfg.retries == {"max_attempts": 1}


# --- fix round 1: fail closed ---------------------------------------------------

def test_unexpected_assume_error_is_unavailable_and_logged(caplog):
    class Boom:
        def assume_role(self, **kw):
            raise KeyError("credential_provider")

    with caplog.at_level(logging.WARNING, logger="app.ai.aws"):
        with pytest.raises(AIUnavailable, match="KeyError"):
            run(aws.bedrock_credentials(role_cfg(), sts_factory=lambda r: Boom()))
    text = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "KeyError" in text and "arn:aws:iam::123:role/r" in text and "ext-1" not in text


@pytest.mark.parametrize("mode", [None, "chain", "bogus"])
def test_tenant_requires_role_or_keys(mode):
    cfg = ProviderConfig("bedrock", "m", "tenant", tenant_id=1, region="r", auth_mode=mode)
    with pytest.raises(AIUnavailable, match="must be role or keys"):
        run(aws.bedrock_credentials(cfg))


@pytest.mark.parametrize("secrets", [{}, {"access_key_id": "AKIA1"}, {"secret_access_key": "S1"}])
def test_keys_mode_missing_keys(secrets):
    cfg = ProviderConfig("bedrock", "m", "tenant", tenant_id=1, region="r", auth_mode="keys", secrets=secrets)
    with pytest.raises(AIUnavailable, match="access keys not configured"):
        run(aws.bedrock_credentials(cfg))


def test_role_mode_without_arn_does_not_call_sts():
    sts = FakeSTS()
    with pytest.raises(AIUnavailable, match="role ARN not configured"):
        run(aws.bedrock_credentials(role_cfg(role_arn=None), sts_factory=lambda r: sts))
    assert sts.calls == []
