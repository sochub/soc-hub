from datetime import datetime, timezone
from typing import Dict, List, Optional

KEYS = ("first_seen", "detected", "contained", "recovered")


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _pick(override, computed):
    if override is not None:
        return {"at": override, "source": "override"}
    if computed is not None:
        return {"at": computed, "source": "computed"}
    return {"at": None, "source": "unknown"}


def _secs(a, b):
    return int((b - a).total_seconds()) if a is not None and b is not None else None


DURATIONS = (("ttd_seconds", "first_seen", "detected"), ("ttc_seconds", "detected", "contained"),
             ("ttr_seconds", "contained", "recovered"))


def compute(*, ioc_first_seen: List[datetime], alert_created: List[datetime], case_created: datetime,
            contained_candidates: List[datetime], resolved_at: Optional[datetime], overrides: Dict) -> Dict:
    ioc_first_seen = [_aware(x) for x in ioc_first_seen]
    alert_created = [_aware(x) for x in alert_created]
    contained_candidates = [_aware(x) for x in contained_candidates]
    case_created, resolved_at = _aware(case_created), _aware(resolved_at)
    overrides = {k: _aware(v) for k, v in overrides.items()}
    early = [x for x in (*ioc_first_seen, *alert_created) if x is not None]
    out = {
        "first_seen": _pick(overrides.get("first_seen"), min(early) if early else None),
        "detected": _pick(overrides.get("detected"), case_created),
        "contained": _pick(overrides.get("contained"), max(contained_candidates) if contained_candidates else None),
        "recovered": _pick(overrides.get("recovered"), resolved_at),
    }
    at = {k: out[k]["at"] for k in KEYS}
    out["out_of_order"] = []
    for name, a, b in DURATIONS:
        v = _secs(at[a], at[b])
        if v is not None and v < 0:
            out["out_of_order"].append(name)
            v = None
        out[name] = v
    return out


def ordered(overrides: Dict) -> bool:
    seq = [_aware(overrides.get(k)) for k in KEYS if overrides.get(k) is not None]
    return all(a <= b for a, b in zip(seq, seq[1:]))


def fmt_duration(seconds: Optional[int]) -> str:
    if seconds is None or seconds < 0:
        return "unknown"
    m = seconds // 60
    d, h, mm = m // 1440, (m % 1440) // 60, m % 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {mm}m"
    return f"{mm}m"
