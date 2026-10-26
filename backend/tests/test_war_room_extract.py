from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.milestones import KEYS
from app.services.war_room_extract import Candidate, extract_candidates, normalize_slack_text

MAD = ZoneInfo("Europe/Madrid")


def _extract(text, at=None, **kw):
    at = at or datetime(2026, 10, 26, 15, 0, tzinfo=MAD)
    return extract_candidates(text, tz=MAD, message_at=at, **kw)


def _artifacts(cands):
    return {(c.payload["type"], c.payload["value"]) for c in cands if c.kind == "artifact"}


def _milestones(cands):
    return {c.payload["key"]: c.payload["at"] for c in cands if c.kind == "milestone"}


def test_normalize_unwraps_links_mentions_entities():
    out = normalize_slack_text("see <https://evil.example/x|click> <@U1> <#C1|ir> <!here> a &amp; b")
    assert out == "see https://evil.example/x a & b"


def test_normalize_unwraps_mailto():
    assert normalize_slack_text("<mailto:a@x.io|a@x.io>") == "a@x.io"


def test_indicators_from_existing_detector():
    cands = _extract("login from 185.220.101.4")
    assert Candidate(kind="artifact", payload={"type": "ip", "value": "185.220.101.4"},
                     value_key="ip:185.220.101.4") in cands


def test_hash_maps_to_file_hash():
    assert ("file_hash", "d41d8cd98f00b204e9800998ecf8427e") in _artifacts(_extract("md5 d41d8cd98f00b204e9800998ecf8427e"))


def test_hostname_patterns():
    found = _artifacts(_extract("WS-FIN-07 beaconed, dc01.corp is down, host fileserver rebooted"))
    assert {("hostname", "WS-FIN-07"), ("hostname", "dc01.corp"), ("hostname", "fileserver")} <= found


def test_internal_hostname_is_not_also_a_domain():
    cands = _extract("check dc01.corp and evil.com")
    assert ("domain", "evil.com") in _artifacts(cands)
    assert not any(t == "domain" and "corp" in v for t, v in _artifacts(cands))


def test_hostname_noise_ignored():
    assert _artifacts(_extract("box3 e-2 x-1 plain words here")) == set()
    assert _artifacts(_extract("see CVE-2024-1234 and host is down")) == set()


def test_username_needs_cue():
    found = _artifacts(_extract("ACME\\jdoe logged in, user asmith, reset password for bwayne"))
    assert ("username", "ACME\\jdoe") in found
    assert ("username", "asmith") in found
    assert ("username", "bwayne") in found
    assert _artifacts(_extract("jdoe said hi")) == set()
    assert _artifacts(_extract("user is locked out")) == set()


def test_email_of_author_excluded():
    assert _artifacts(_extract("mail ana@x.io and bob@y.io", exclude_emails={"ana@x.io"})) == {("email", "bob@y.io")}


def test_dedupe_by_value_key():
    cands = _extract("185.220.101.4 then 185.220.101.4 again")
    assert [c.value_key for c in cands].count("ip:185.220.101.4") == 1


def test_milestone_contained_today():
    m = _milestones(_extract("contained at 14:32"))
    assert m == {"contained": "2026-10-26T14:32:00+01:00"}
    assert set(m) <= set(KEYS)


def test_milestone_pm_and_utc():
    assert _milestones(_extract("detected 2:05pm")) == {"detected": "2026-10-26T14:05:00+01:00"}
    assert _milestones(_extract("recovered at 09:10 UTC")) == {"recovered": "2026-10-26T09:10:00+00:00"}
    assert _milestones(_extract("isolated 08:00Z")) == {"contained": "2026-10-26T08:00:00+00:00"}
    assert _milestones(_extract("restored 09:00"))["recovered"].startswith("2026-10-26T09:00")


def test_bare_time_after_message_rolls_back_a_day():
    at = datetime(2026, 10, 26, 0, 10, tzinfo=MAD)
    assert _milestones(_extract("contained at 23:50", at)) == {"contained": "2026-10-25T23:50:00+01:00"}


def test_yesterday_keyword():
    assert _milestones(_extract("first seen yesterday 22:00")) == {"first_seen": "2026-10-25T22:00:00+01:00"}


def test_no_milestone_without_keyword():
    assert _milestones(_extract("meeting at 14:32")) == {}
    assert _milestones(_extract("contained the situation, and later we met at 14:32 sharp")) == {}


def test_invalid_time_ignored():
    assert _milestones(_extract("detected at 25:99")) == {}


# ---- fix round 1: bounded cost and noise ------------------------------------

import time

import pytest


@pytest.mark.parametrize("text", [
    "a-" * 20000,
    "a.a" * 13000 + "!",
    "A-" * 20000 + "1x",
    "a" + ".a" * 20000 + "!",
    "a.a " * 10000,
    "host " * 8000,
    "user " + "a" * 40000,
    "<" * 40000,
    "a\\b " * 10000,
])
def test_adversarial_input_is_fast(text):
    start = time.perf_counter()
    _extract(text)
    assert time.perf_counter() - start < 0.5


@pytest.mark.parametrize("text", [
    "COVID-19 Windows-10 Python-3 TCP-443 Office-365 win-2019",
    "host machine endpoint protection; endpoint security; machine learning",
    "the user account is compromised; account takeover; user can't login; login page",
    "reset for example",
    r"C:\Users\bob and \\fileserver\share",
    "report.ad build.local notes.local",
])
def test_ordinary_words_are_not_artifacts(text):
    assert _artifacts(_extract(text)) == set()


def test_internal_fqdn_with_labels_still_found():
    assert ("hostname", "fileserver.corp.local") in _artifacts(_extract("see fileserver.corp.local"))


def test_cue_host_with_public_domain_stays_a_domain():
    found = _artifacts(_extract("host: evil.com, host 10.0.0.5"))
    assert ("domain", "evil.com") in found
    assert ("ip", "10.0.0.5") in found
    assert not any(t == "hostname" for t, _ in found)
