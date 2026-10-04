"""Tests for RFC 6238 TOTP implementation."""
import pytest
from app.core.totp import (
    new_secret,
    totp_at,
    matching_step,
    provisioning_uri,
    consume_step,
)


class FakeRedis:
    """Minimal async redis mock for consume_step testing."""

    def __init__(self):
        self.used = set()

    async def set(self, key, val, nx=False, ex=None):
        """Returns True on first use, None on replay."""
        if key in self.used:
            return None
        self.used.add(key)
        return True


class TestNewSecret:
    """Test secret generation."""

    def test_new_secret_length(self):
        """Secrets should be 32 base32 characters (160 bits)."""
        secret = new_secret()
        assert len(secret) == 32
        assert secret.isupper()
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret)


class TestTotpAt:
    """RFC 6238 Appendix B test vectors (SHA-1, 30s steps)."""

    # Secret: ASCII "12345678901234567890" -> base32 GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ
    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def test_vector_t59(self):
        """T=59 -> 94287082 (8 digits)."""
        assert totp_at(self.SECRET, 59, digits=8) == "94287082"

    def test_vector_t1111111109(self):
        """T=1111111109 -> 07081804 (8 digits)."""
        assert totp_at(self.SECRET, 1111111109, digits=8) == "07081804"

    def test_vector_t1111111111(self):
        """T=1111111111 -> 14050471 (8 digits)."""
        assert totp_at(self.SECRET, 1111111111, digits=8) == "14050471"

    def test_vector_t1234567890(self):
        """T=1234567890 -> 89005924 (8 digits)."""
        assert totp_at(self.SECRET, 1234567890, digits=8) == "89005924"

    def test_vector_t2000000000(self):
        """T=2000000000 -> 69279037 (8 digits)."""
        assert totp_at(self.SECRET, 2000000000, digits=8) == "69279037"


class TestMatchingStep:
    """Test TOTP window validation and constant-time comparison."""

    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def test_matching_step_success(self):
        """Valid code returns the matching step."""
        # At T=1234567890, get the 6-digit code
        code = totp_at(self.SECRET, 1234567890, digits=6)
        step = matching_step(self.SECRET, code, now=1234567890)
        assert step is not None
        assert isinstance(step, int)

    def test_matching_step_default_digits(self):
        """Default 6 digits for matching."""
        # Get a valid 6-digit code at a known time
        code = totp_at(self.SECRET, 100, digits=6)
        step = matching_step(self.SECRET, code, now=100)
        assert step is not None

    def test_window_minus_30(self):
        """Code from now-30 should match (within ±1 step window)."""
        # At T=1000, code is for T=1000
        now = 1000
        code_prev = totp_at(self.SECRET, now - 30, digits=6)
        step = matching_step(self.SECRET, code_prev, now=now)
        # now-30 is exactly 1 step back, should match
        assert step is not None

    def test_window_plus_30(self):
        """Code from now+30 should match (within ±1 step window)."""
        now = 1000
        code_next = totp_at(self.SECRET, now + 30, digits=6)
        step = matching_step(self.SECRET, code_next, now=now)
        # now+30 is exactly 1 step forward, should match
        assert step is not None

    def test_window_minus_60_fails(self):
        """Code from now-60 should not match (outside ±1 window)."""
        now = 1000
        code_old = totp_at(self.SECRET, now - 60, digits=6)
        step = matching_step(self.SECRET, code_old, now=now)
        # now-60 is 2 steps back, outside window
        assert step is None

    def test_window_plus_60_fails(self):
        """Code from now+60 should not match (outside ±1 window)."""
        now = 1000
        code_future = totp_at(self.SECRET, now + 60, digits=6)
        step = matching_step(self.SECRET, code_future, now=now)
        # now+60 is 2 steps forward, outside window
        assert step is None

    def test_invalid_non_digits(self):
        """Non-digit codes return None."""
        assert matching_step(self.SECRET, "abc123", now=100) is None
        assert matching_step(self.SECRET, "12a456", now=100) is None

    def test_invalid_wrong_length(self):
        """Wrong-length codes return None."""
        assert matching_step(self.SECRET, "123", now=100) is None
        assert matching_step(self.SECRET, "12345678", now=100) is None

    def test_invalid_empty(self):
        """Empty code returns None."""
        assert matching_step(self.SECRET, "", now=100) is None

    def test_invalid_none(self):
        """None code returns None."""
        assert matching_step(self.SECRET, None, now=100) is None

    def test_valid_code_with_whitespace(self):
        """Codes with leading/trailing whitespace are stripped and matched."""
        # Get a valid code and wrap it with whitespace
        code = totp_at(self.SECRET, 100, digits=6)
        step = matching_step(self.SECRET, f"  {code}  ", now=100)
        assert step is not None


class TestProvisioningUri:
    """Test otpauth URI generation."""

    SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def test_uri_format(self):
        """URI should be otpauth://totp/SOC%20Hub:<email>?secret=...&issuer=SOC%20Hub."""
        email = "test@example.com"
        uri = provisioning_uri(self.SECRET, email)
        assert uri.startswith("otpauth://totp/SOC%20Hub:")
        assert f"?secret={self.SECRET}" in uri
        assert "issuer=SOC%20Hub" in uri

    def test_uri_email_encoding(self):
        """Email should be properly URL-encoded."""
        email = "user+tag@example.com"
        uri = provisioning_uri(self.SECRET, email)
        assert "user%2Btag%40example.com" in uri

    def test_uri_simple_email(self):
        """Simple email without special chars."""
        email = "alice@example.com"
        uri = provisioning_uri(self.SECRET, email)
        assert "alice@example.com" in uri or "alice%40example.com" in uri


class TestConsumeStep:
    """Test replay protection with fake redis."""

    @pytest.mark.asyncio
    async def test_consume_step_first_use(self):
        """First use of a step returns True."""
        redis = FakeRedis()
        result = await consume_step(redis, user_id=42, step=1000)
        assert result is True

    @pytest.mark.asyncio
    async def test_consume_step_replay_fails(self):
        """Replaying a used step returns False."""
        redis = FakeRedis()
        # First use
        result1 = await consume_step(redis, user_id=42, step=1000)
        assert result1 is True
        # Replay
        result2 = await consume_step(redis, user_id=42, step=1000)
        assert result2 is False

    @pytest.mark.asyncio
    async def test_consume_step_different_users(self):
        """Different users can use the same step."""
        redis = FakeRedis()
        result1 = await consume_step(redis, user_id=42, step=1000)
        result2 = await consume_step(redis, user_id=43, step=1000)
        assert result1 is True
        assert result2 is True

    @pytest.mark.asyncio
    async def test_consume_step_different_steps(self):
        """Same user can use different steps."""
        redis = FakeRedis()
        result1 = await consume_step(redis, user_id=42, step=1000)
        result2 = await consume_step(redis, user_id=42, step=1001)
        assert result1 is True
        assert result2 is True
