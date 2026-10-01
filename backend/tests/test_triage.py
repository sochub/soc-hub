from app.tasks.triage import best_matching_category
from app.services.ai_service import parse_triage_payload


def test_matches_phishing_from_description():
    assert best_matching_category(
        "Suspicious email reported",
        "User forwarded a suspicious email with a malicious link asking for credential harvest.",
        [],
    ) == "phishing"


def test_matches_ransomware_over_malware_on_stronger_signal():
    assert best_matching_category(
        "Files encrypted",
        "Multiple hosts show a ransom note; files encrypted, extortion demand received.",
        [],
    ) == "ransomware"


def test_no_match_returns_none():
    assert best_matching_category("Routine review", "Nothing unusual to report.", []) is None


def test_tags_are_included_in_matching():
    assert best_matching_category("Incident", "", ["password spray", "repeated login"]) == "credential-access"


def test_parse_triage_payload_valid():
    result = parse_triage_payload({
        "severity": "HIGH", "tags": [" phishing ", "email", ""], "next_steps": " Isolate the host. ",
    })
    assert result == {"severity": "high", "tags": ["phishing", "email"], "next_steps_text": "Isolate the host."}


def test_parse_triage_payload_invalid_severity_dropped():
    result = parse_triage_payload({"severity": "not-a-severity", "tags": ["x"], "next_steps": ""})
    assert result == {"severity": None, "tags": ["x"], "next_steps_text": None}


def test_parse_triage_payload_empty_returns_none():
    assert parse_triage_payload({"severity": "bogus", "tags": [], "next_steps": ""}) is None
    assert parse_triage_payload({}) is None
    assert parse_triage_payload("not a dict") is None


def test_parse_triage_payload_tags_capped_at_eight():
    result = parse_triage_payload({"tags": [str(i) for i in range(20)]})
    assert result is not None
    assert len(result["tags"]) == 8
