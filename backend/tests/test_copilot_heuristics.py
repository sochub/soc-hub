from app.utils.copilot_heuristics import (
    detect_indicators, extract_note_text, is_note_request, is_declarative_finding,
    is_meta_note,
)


def test_detect_ip_and_domain():
    out = detect_indicators("beaconing from 185.220.101.45 to evil-c2.support observed")
    types = {d["artifact_type"]: d["value"] for d in out}
    assert types["ip"] == "185.220.101.45"
    assert types["domain"] == "evil-c2.support"


def test_detect_skips_invalid_ip_and_dedupes():
    out = detect_indicators("999.999.999.999 then 8.8.8.8 and again 8.8.8.8")
    assert [d["value"] for d in out] == ["8.8.8.8"]


def test_url_consumes_domain():
    out = detect_indicators("payload at http://drive-share-files.net/d/invoice.zip")
    assert len(out) == 1 and out[0]["artifact_type"] == "url"


def test_detect_hash_and_email():
    out = detect_indicators("hash d41d8cd98f00b204e9800998ecf8427e from billing@evil.com")
    types = {d["artifact_type"] for d in out}
    assert "file_hash" in types and "email" in types


def test_extract_note_colon():
    assert extract_note_text("add a comment: user clicked the link on their phone") == \
        "user clicked the link on their phone"


def test_extract_note_that():
    assert extract_note_text("add a note that we blocked the sender domain") == \
        "we blocked the sender domain"


def test_extract_note_none_for_question():
    assert extract_note_text("what should I do next?") is None


def test_is_note_request():
    assert is_note_request("please add a comment: done")
    assert is_note_request("record a log entry about the containment")
    assert not is_note_request("what is an IOC?")


def test_declarative_finding():
    assert is_declarative_finding("We isolated the host and reset the user credentials this morning.")
    assert not is_declarative_finding("what happened to the host?")
    assert not is_declarative_finding("ok thanks")


# --- the case-44 failure modes -------------------------------------------

def test_activity_log_inline_content():
    msg = 'add an activity log i just chat with the user "marcelo.fernandez@domain.org" and he mentioned he is the only one who received it'
    text = extract_note_text(msg)
    assert text is not None
    assert "marcelo.fernandez@domain.org" in text
    assert "only one who received" in text


def test_activity_log_is_note_request():
    assert is_note_request("add an activity log i just chat with the user")
    assert is_note_request("add the activity log")
    assert is_note_request("record a timeline entry: contained the host")


def test_referential_request_has_no_explicit_text():
    # "add the activity log" alone → content must be generated, not extracted
    assert extract_note_text("add the activity log") is None
    assert extract_note_text("add a note to the case") is None


def test_meta_note_detection():
    assert is_meta_note("Added activity log")
    assert is_meta_note("added activity log.")
    assert is_meta_note("Adding a note")
    assert is_meta_note("Note added")
    assert is_meta_note("Recorded the log entry")
    assert not is_meta_note("user confirmed they clicked the link on their phone")
    assert not is_meta_note("Marcelo mentioned that he is the only one who received the phishing message.")
