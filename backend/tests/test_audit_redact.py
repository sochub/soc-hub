from app.audit.redact import email_domain, scrub


def test_scrub_masks_sensitive_keys_at_any_depth():
    src = {"title": "ok", "Password": "p", "config": {"api_key": "k", "nested": [{"apiKey": "k2"}, {"x": 1}]},
           "value_enc": "e", "mfa_secret": "m", "Authorization": "Bearer t", "items": ["plain"],
           "client_secret": "s", "refresh_token": "r", "credentials": {"a": 1}}
    out = scrub(src)
    assert out["title"] == "ok" and out["items"] == ["plain"] and out["config"]["nested"][1] == {"x": 1}
    for path in [("Password",), ("config", "api_key"), ("value_enc",), ("mfa_secret",), ("Authorization",),
                 ("client_secret",), ("refresh_token",), ("credentials",)]:
        v = out
        for p in path:
            v = v[p]
        assert v == "***", path
    assert out["config"]["nested"][0]["apiKey"] == "***"
    assert src["Password"] == "p"  # input untouched


def test_scrub_passes_scalars_and_none():
    assert scrub(None) is None and scrub("x") == "x" and scrub(3) == 3


def test_email_domain():
    assert email_domain("Alice@Example.COM") == "example.com"
    assert email_domain("no-at-sign") is None and email_domain("") is None


def test_scrub_keeps_name_lists_under_sensitive_metadata_keys():
    names = {"secrets_updated": ["api_key"], "credentials_updated": ["virustotal_api_key"]}
    assert scrub(names) == names
    assert scrub({"secrets_updated": ["sk-Live-ABC"]}) == {"secrets_updated": "***"}
    assert scrub({"api_key_fields": "x"}) == {"api_key_fields": "***"}


def test_name_list_identifier_check_is_a_fullmatch():
    assert scrub({"secrets_updated": ["api_key\n"]}) == {"secrets_updated": "***"}


def test_name_list_exemption_only_for_flat_string_lists():
    assert scrub({"secrets_updated": ("api_key",)}) == {"secrets_updated": "***"}  # tuple
    assert scrub({"secrets_updated": ["api_key", None]}) == {"secrets_updated": "***"}  # list with None
    assert scrub({"secrets_updated": [["api_key"]]}) == {"secrets_updated": "***"}  # nested list
    assert scrub({"secrets_updated": []}) == {"secrets_updated": []}  # a valid, empty name list
