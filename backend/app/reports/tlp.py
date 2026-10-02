import re
from typing import Optional

ORDER = ("white", "green", "amber", "red")
LABEL = {"white": "CLEAR", "green": "GREEN", "amber": "AMBER", "red": "RED"}
COLORS = {"white": ("#FFFFFF", "#000000"), "green": ("#33FF00", "#000000"),
          "amber": ("#FFC000", "#000000"), "red": ("#FF2B2B", "#FFFFFF")}
_WS = re.compile(r"\s+")


def normalize(v) -> Optional[str]:
    """Canonical TLP name ("white"/"green"/"amber"/"red") or None for None/empty/unknown input.

    Case-insensitive; drops a "tlp:" prefix and any whitespace; TLP 2.0 "clear" maps to "white"."""
    if v is None:
        return None
    s = _WS.sub("", str(v)).lower()
    if s.startswith("tlp:"):
        s = s[4:]
    if s == "clear":
        s = "white"
    return s if s in ORDER else None


def floor(tlps) -> str:
    """Highest TLP among the values. Fail closed: any non-empty value that isn't a TLP counts as red."""
    vals = []
    for t in tlps:
        if t is None or not str(t).strip():
            continue
        vals.append(normalize(t) or "red")
    return max(vals, key=ORDER.index) if vals else "white"


def at_least(t, minimum) -> bool:
    a, b = normalize(t), normalize(minimum)
    return a is not None and b is not None and ORDER.index(a) >= ORDER.index(b)
