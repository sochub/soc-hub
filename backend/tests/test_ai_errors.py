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
