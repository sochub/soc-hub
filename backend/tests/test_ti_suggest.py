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
