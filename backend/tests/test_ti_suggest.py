from app.enrichment.suggest import suggest


def r(source, verdict, summary=None, status="ok"):
    return {"source": source, "status": status, "verdict": verdict, "summary": summary or {}}


def test_critical_needs_vt15_and_abusech_hit():
    res = [r("virustotal", "malicious", {"malicious": 20}), r("threatfox", "malicious", {"malware_printable": "Emotet"})]
    assert suggest("medium", [], res) == {"threat_level": "critical", "tags": ["malware:emotet"]}


def test_high_on_any_malicious():
    assert suggest("low", [], [r("virustotal", "malicious", {"malicious": 5})])["threat_level"] == "high"


def test_medium_on_suspicious_only():
    assert suggest("low", [], [r("rdap", "suspicious")]) == {"threat_level": "medium", "tags": []}


def test_never_lowers_and_none_when_nothing_new():
    assert suggest("critical", [], [r("virustotal", "malicious", {"malicious": 5})]) is None
    assert suggest("high", ["malware:emotet"], [r("threatfox", "malicious", {"malware_printable": "Emotet"})]) is None


def test_tags_dedup_cap_and_order():
    res = [r("threatfox", "malicious", {"malware_printable": "QakBot"}),
           r("urlhaus", "malicious", {"threat": "malware_download"}),
           r("virustotal", "malicious", {"malicious": 3, "popular_threat_label": "Trojan.QakBot/Generic"})]
    out = suggest("high", ["urlhaus:malware_download"], res)
    assert out == {"threat_level": None, "tags": ["malware:qakbot", "trojan.qakbot/generic"]}


def test_ignores_non_ok_results():
    assert suggest("low", [], [r("virustotal", "malicious", {"malicious": 50}, status="error")]) is None


def test_junk_types_do_not_crash():
    res = [{"status": "ok", "verdict": "malicious", "summary": {"malicious": "lots", "popular_threat_label": 5}},
           {"source": "threatfox", "status": "ok", "verdict": "malicious", "summary": {"malware_printable": 7}},
           {"source": "urlhaus", "status": "ok", "verdict": None, "summary": "oops"},
           {"source": "virustotal", "status": "ok", "verdict": "malicious", "summary": {"malicious": "x", "popular_threat_label": ["a"]}}]
    assert suggest("low", [], res) == {"threat_level": "high", "tags": []}
