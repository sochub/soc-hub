import pytest

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


@pytest.mark.parametrize("message, is_request, literal", [
    # literal text: taken verbatim from the message
    ("Add a note that the syslog-ng server on host-1 was restarted", True,
     "the syslog-ng server on host-1 was restarted"),
    ("Add a note that the analyst reviewed the catalog - nothing unusual", True,
     "the analyst reviewed the catalog - nothing unusual"),
    ("Record that the user's mailbox was purged and the sender's domain blocked", True,
     "the user's mailbox was purged and the sender's domain blocked"),
    ('add a note "we blocked the sender at the gateway"', True, "we blocked the sender at the gateway"),
    ("record a timeline entry: contained the host", True, "contained the host"),
    # a request to compose: a note request with no literal text (the model writes it)
    ("Create a timeline of the attack", True, None),
    ("Write a comment summarizing the phishing analysis", True, None),
    ("add this to the timeline", True, None),
    # not note requests at all
    ("log in to the firewall and check the timeline", False, None),
    ("create a case and note the IP 1.2.3.4", False, None),
    ("Add 1.2.3.4 as an artifact and log it", False, None),
    ("create a case and add a note that the user clicked", False, "the user clicked"),
])
def test_note_phrases(message, is_request, literal):
    assert is_note_request(message) is is_request
    assert extract_note_text(message) == literal


@pytest.mark.parametrize("message, finding", [
    ("We isolated the host and reset the user credentials this morning.", True),
    ("Blocked the sender domain at the gateway and purged 14 mailboxes.", True),
    ("Update the severity to high and close the case please", False),
    ("Please summarize what we know so far about this incident", False),
    ("The user says the laptop was stolen yesterday, can you check please", False),
])
def test_declarative_finding_ignores_commands_and_questions(message, finding):
    assert is_declarative_finding(message) is finding


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


def test_detect_indicators_is_linear_on_pathological_input():
    """Tracked ReDoS: inputs that made the email/domain regexes quadratic finish well under a second, oversized
    text is truncated, and real indicators still match."""
    import time
    from app.utils.copilot_heuristics import MAX_SCAN_CHARS
    n = 64_000
    for text in ("a" * n, "a." * (n // 2), "a@" + "a." * (n // 2), "a@a." * (n // 4), "a-" * (n // 2),
                 "x" * 10 * n, ("a." * 40 + "@") * (n // 82)):
        t0 = time.perf_counter()
        detect_indicators(text, limit=20)
        assert time.perf_counter() - t0 < 1, text[:20]
    got = {i["value"] for i in detect_indicators("mail bob.smith+x@mail.evil-corp.co.uk then evil-corp.com", limit=10)}
    assert {"bob.smith+x@mail.evil-corp.co.uk", "evil-corp.com"} <= got
    assert detect_indicators("a" * MAX_SCAN_CHARS + " 8.8.8.8") == []  # past the scan window


def test_copilot_message_length_capped():
    import pytest
    from pydantic import ValidationError
    from app.schemas.copilot import CopilotChatRequest
    CopilotChatRequest(message="x" * 16_000)
    with pytest.raises(ValidationError):
        CopilotChatRequest(message="x" * 16_001)


def test_ground_action_keeps_only_what_was_asked():
    from app.utils.copilot_heuristics import ground_action
    ST, SV = ("new", "open", "in_progress", "pending", "resolved", "closed"), ("critical", "high", "medium", "low", "info")

    def g(action_type, params, asked):
        out = ground_action({"type": action_type, "summary": "s", "params": params}, asked, statuses=ST, severities=SV)
        return out and out["params"]

    full = {"status": "open", "severity": "high", "tags": ["data exfiltration"]}
    # the model filled every field of a "tag this" request (and invented a status)
    assert g("update_case", full, "Tag this as data exfiltration") == {"tags": ["data exfiltration"]}
    assert g("update_case", {**full, "status": "exfiltration_tagged"}, "tag this case a data exfiltration") \
        == {"tags": ["data exfiltration"]}
    # each field survives when it was asked for
    assert g("update_case", full, "raise the severity to high") == {"severity": "high"}
    assert g("update_case", {"status": "resolved", "severity": "low"}, "mark this as resolved") == {"status": "resolved"}
    assert g("update_case", {"status": "open"}, "please reopen the case") == {"status": "open"}
    assert g("update_case", full, "set it to open, make it high priority and tag it") == full
    # the turn before counts: "yes" keeps what the assistant offered
    assert g("update_case", {"severity": "critical"}, "Should I raise the severity to critical? yes do it") \
        == {"severity": "critical"}
    # nothing asked-for left: no action at all
    assert g("update_case", {"status": "open", "severity": "medium"}, "what happened here?") is None
    assert g("update_case", {"status": "bogus"}, "close it") is None
    # find_related: an invented value is dropped (the case's own indicators are used), a given one is kept
    assert g("find_related", {"value": "phishing campaign"}, "find related cases") == {}
    assert g("find_related", {"value": "evil.com"}, "which cases share hxxp://evil[.]com ?") == {"value": "evil.com"}
    # words from an earlier turn are not a search value; an indicator from an earlier turn is
    def gm(params, asked, message):
        return ground_action({"type": "find_related", "params": params}, asked, statuses=ST, severities=SV,
                             message=message)["params"]
    assert gm({"value": "data exfiltration"}, "tag this case a data exfiltration find related cases", "find related cases") == {}
    assert gm({"value": "203.0.113.77"}, "it beacons to 203.0.113.77 find related cases", "find related cases") \
        == {"value": "203.0.113.77"}
    assert gm({"value": "WS-042"}, "find cases related to WS-042", "find cases related to WS-042") == {"value": "WS-042"}
    # other actions pass through untouched
    assert g("add_artifact", {"value": "1.2.3.4"}, "anything") == {"value": "1.2.3.4"}
    assert ground_action(None, "x", statuses=ST, severities=SV) is None


def test_related_request_and_affirmation():
    from app.utils.copilot_heuristics import is_affirmation, related_request_value as rel
    for msg in ("find related cases", "Are there similar cases?", "any other cases sharing this IP?",
                "correlate this with the rest of the queue", "have we seen this before?"):
        assert rel(msg) == "", msg
    assert rel("which cases share hxxp://evil[.]com/x ?") == "http://evil.com/x"
    assert rel("find cases related to 203.0.113.77") == "203.0.113.77"
    for msg in ("tag this case a data exfiltration", "add a note that we closed the related ticket", "what is this case?"):
        assert rel(msg) is None, msg
    assert is_affirmation("yes do it") and is_affirmation("ok, go ahead") and is_affirmation("Sure")
    assert not is_affirmation("find related cases") and not is_affirmation("yes but first tell me everything about the host and the user and why")


def test_explicit_case_update():
    from app.utils.copilot_heuristics import explicit_case_update as up
    assert up("raise the severity to high") == {"severity": "high"}
    assert up("Set severity: critical please") == {"severity": "critical"}
    assert up("escalate this to critical and mark it as in progress") == {"severity": "critical", "status": "in_progress"}
    assert up("mark this as resolved") == {"status": "resolved"}
    assert up("change the status to pending") == {"status": "pending"}
    assert up("please reopen the case") == {"status": "open"}
    for msg in ("should we raise the severity to high?", "why is the severity high", "tag this as high-value target",
                "the status page is open", "what is this case about"):
        assert up(msg) is None, msg
