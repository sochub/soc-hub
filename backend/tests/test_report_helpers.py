from datetime import datetime, timedelta, timezone

import pytest

from app.reports import defang as d, milestones as ms, tlp

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_tlp_floor_and_at_least():
    assert tlp.floor([]) == "white" and tlp.floor([None, "bogus"]) == "white"
    assert tlp.floor(["green", "red", "amber"]) == "red"
    assert tlp.at_least("amber", "green") and not tlp.at_least("green", "amber")
    assert tlp.LABEL["white"] == "CLEAR" and tlp.COLORS["red"] == ("#FF2B2B", "#FFFFFF")


@pytest.mark.parametrize("value,itype,want", [
    ("http://evil.com/a.b", "url", "hxxp://evil[.]com/a.b"),
    ("https://x.y.com", "url", "hxxps://x[.]y[.]com"),
    ("evil.co.uk", "domain", "evil[.]co[.]uk"),
    ("1.2.3.4", "ip_address", "1.2.3[.]4"),
    ("2001:db8::1", "ip", "2001[:]db8[:][:]1"),
    ("a.b@evil.com", "email", "a.b[@]evil[.]com"),
    ("d41d8cd98f00b204e9800998ecf8427e", "file_hash", "d41d8cd98f00b204e9800998ecf8427e"),
    ("HKLM\\Run", "registry_key", "HKLM\\Run"),
])
def test_defang(value, itype, want):
    assert d.defang(value, itype) == want


def test_milestones_computed_override_unknown():
    out = ms.compute(ioc_first_seen=[T0 - timedelta(days=2)], alert_created=[T0 - timedelta(days=1)],
                     case_created=T0, contained_candidates=[T0 + timedelta(hours=3), T0 + timedelta(hours=6)],
                     resolved_at=None, overrides={"recovered": None})
    assert out["first_seen"] == {"at": T0 - timedelta(days=2), "source": "computed"}
    assert out["detected"]["source"] == "computed" and out["contained"]["at"] == T0 + timedelta(hours=6)
    assert out["recovered"] == {"at": None, "source": "unknown"}
    assert out["ttd_seconds"] == 2 * 86400 and out["ttc_seconds"] == 6 * 3600 and out["ttr_seconds"] is None
    o2 = ms.compute(ioc_first_seen=[], alert_created=[], case_created=T0, contained_candidates=[],
                    resolved_at=T0 + timedelta(days=1), overrides={"first_seen": T0 - timedelta(hours=1)})
    assert o2["first_seen"]["source"] == "override" and o2["contained"]["source"] == "unknown"
    assert o2["ttd_seconds"] == 3600 and o2["ttc_seconds"] is None and o2["ttr_seconds"] is None


def test_ordered_and_fmt():
    assert ms.ordered({"first_seen": T0, "detected": T0 + timedelta(1), "contained": None, "recovered": T0 + timedelta(3)})
    assert not ms.ordered({"detected": T0 + timedelta(1), "contained": T0})
    assert [ms.fmt_duration(x) for x in (None, 2700, 22320, 187200)] == ["unknown", "45m", "6h 12m", "2d 4h"]


def test_negative_durations_are_unknown():
    out = ms.compute(ioc_first_seen=[], alert_created=[], case_created=T0,
                     contained_candidates=[T0 - timedelta(hours=1)], resolved_at=None, overrides={})
    assert out["ttc_seconds"] is None and out["out_of_order"] == ["ttc_seconds"]
    out = ms.compute(ioc_first_seen=[], alert_created=[], case_created=T0,
                     contained_candidates=[T0 + timedelta(hours=5)], resolved_at=T0 + timedelta(hours=2), overrides={})
    assert out["ttr_seconds"] is None and out["ttc_seconds"] == 5 * 3600 and out["out_of_order"] == ["ttr_seconds"]
    assert ms.fmt_duration(-5) == "unknown"


def test_naive_and_aware_mix():
    naive = T0.replace(tzinfo=None)
    out = ms.compute(ioc_first_seen=[naive - timedelta(hours=1)], alert_created=[], case_created=T0,
                     contained_candidates=[naive + timedelta(hours=1)], resolved_at=naive + timedelta(hours=2),
                     overrides={"detected": naive})
    assert out["ttd_seconds"] == 3600 and out["ttc_seconds"] == 3600 and out["out_of_order"] == []
    assert ms.ordered({"first_seen": naive, "detected": T0 + timedelta(1)})


def test_defang_ipv6_url_aliases_and_misc():
    assert d.defang("http://[2001:db8::1]/x", "url") == "hxxp://[2001[:]db8[:][:]1]/x"
    assert d.defang("HTTPS://Evil.com/a", "url") == "hxxps://Evil[.]com/a"
    assert d.defang("1.2.3.4", "ipv4") == "1.2.3[.]4" and d.defang("::1", "ipv6") == "[:][:]1"
    assert d.defang("a.evil.com", "hostname") == "a[.]evil[.]com" and d.defang("a.evil.com", "fqdn") == "a[.]evil[.]com"
    assert d.defang(1234, "other") == "1234"


def test_at_least_invalid_minimum():
    assert tlp.at_least("red", "bogus") is False
