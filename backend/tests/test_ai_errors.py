from app.ai.errors import AIError, AIUnavailable, scrub


def test_unavailable_is_ai_error():
    assert issubclass(AIUnavailable, AIError)


def test_scrub_known_secret_values():
    out = scrub("auth failed for key super-secret-value-123", ["super-secret-value-123"])
    assert "super-secret-value-123" not in out and "***" in out


def test_scrub_patterns():
    text = (
        "openai sk-proj-ABCDEFGHIJKLMNOP1234 anthropic sk-ant-api03-XYZXYZXYZXYZ "
        "aws AKIAABCDEFGHIJKLMNOP google AIzaSyA1234567890abcdefghijklmnop "
        "hdr Bearer abc.def.ghi "
        '"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----\\n"'
    )
    out = scrub(text)
    for leaked in ("sk-proj-ABCDEFGHIJKLMNOP1234", "sk-ant-api03-XYZXYZXYZXYZ", "AKIAABCDEFGHIJKLMNOP",
                   "AIzaSyA1234567890abcdefghijklmnop", "abc.def.ghi", "MIIE"):
        assert leaked not in out


def test_scrub_truncates():
    assert len(scrub("x" * 5000)) <= 300


def test_scrub_ignores_blank_secrets():
    assert scrub("hello", ["", None]) == "hello"


import pytest


@pytest.mark.parametrize("text", [
    "task-management-system", "risk-assessment", "desk-top-computer", "Bearer token is missing",
])
def test_scrub_leaves_benign_text(text):
    assert scrub(text) == text


@pytest.mark.parametrize("text,secret", [
    ("tok ya29.a0AfH6SMBxABCDEF-ghi_jkl", "a0AfH6SMBx"),
    ("rt 1//0gABCDEFGHIJKLMNOPQRSTUV", "0gABCDEFGHIJKLMNOPQRSTUV"),
    ("x-api-key: abcDEF1234567890", "abcDEF1234567890"),
    ("api_key=abcdef123456", "abcdef123456"),
    ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("X-Amz-Security-Token=FwoGZXIvYXdzEJr", "FwoGZXIvYXdzEJr"),
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ('{"api_key": "zzzz1234"}', "zzzz1234"),
])
def test_scrub_credential_forms(text, secret):
    assert secret not in scrub(text)


def test_scrub_longest_secret_first():
    out = scrub("abcdefgh and abcdefghijkl", ["abcdefgh", "abcdefghijkl"])
    assert "ijkl" not in out


def test_blank_ai_provider_is_none():
    from app.core.config import Settings
    assert Settings(AI_PROVIDER="  ").AI_PROVIDER is None
    assert Settings(AI_PROVIDER="").AI_PROVIDER is None
    assert Settings(AI_PROVIDER="openai").AI_PROVIDER == "openai"
