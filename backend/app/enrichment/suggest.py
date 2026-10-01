"""Suggestion rules: raise-only threat level + new tags from enrichment results."""
from typing import List, Optional

LEVELS = ("info", "low", "medium", "high", "critical")


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else LEVELS.index("medium")


def suggest(current_level: str, current_tags: List[str], results: List[dict]) -> Optional[dict]:
    ok = [r for r in results if r.get("status") == "ok"]
    by = {r["source"]: r for r in ok}
    vt_mal = int((by.get("virustotal", {}).get("summary") or {}).get("malicious") or 0)
    abuse_hit = any(by.get(s, {}).get("verdict") == "malicious" for s in ("urlhaus", "threatfox"))
    verdicts = {r.get("verdict") for r in ok}
    level = None
    if vt_mal >= 15 and abuse_hit:
        level = "critical"
    elif "malicious" in verdicts:
        level = "high"
    elif "suspicious" in verdicts:
        level = "medium"
    if level and _rank(level) <= _rank(current_level):
        level = None

    have = {t.lower() for t in current_tags or []}
    candidates = []
    tf = (by.get("threatfox", {}).get("summary") or {}).get("malware_printable")
    if tf:
        candidates.append(f"malware:{tf.lower()}")
    uh = (by.get("urlhaus", {}).get("summary") or {}).get("threat")
    if uh:
        candidates.append(f"urlhaus:{uh.lower()}")
    vt_label = (by.get("virustotal", {}).get("summary") or {}).get("popular_threat_label")
    if vt_label:
        candidates.append(vt_label.lower())
    tags = []
    for t in candidates:
        if t not in have and t not in tags:
            tags.append(t)
    tags = tags[:5]
    if not level and not tags:
        return None
    return {"threat_level": level, "tags": tags}
