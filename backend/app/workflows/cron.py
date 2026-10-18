"""Minimal 5-field cron matcher (minute hour dom month dow), UTC, minute precision.

# ponytail: no names (JAN/MON), no L/W/#; add croniter if anyone asks.
Like classic cron, when both dom and dow are restricted a day matches if EITHER does.
"""
from datetime import datetime
from typing import List, Optional, Set

_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day of month", 1, 31), ("month", 1, 12), ("day of week", 0, 7))


def _parse_field(text: str, lo: int, hi: int) -> Set[int]:
    out: Set[int] = set()
    for part in text.split(","):
        rng, _, step_s = part.partition("/")
        step = int(step_s) if step_s else 1
        if step < 1:
            raise ValueError(f"step must be >= 1 in '{part}'")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a_s, b_s = rng.split("-", 1)
            a, b = int(a_s), int(b_s)
        else:
            a = b = int(rng)
        if not (lo <= a <= hi and lo <= b <= hi) or a > b:
            raise ValueError(f"'{part}' out of range {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def _parse(expr: str) -> List[Set[int]]:
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError(f"expected 5 fields (minute hour day-of-month month day-of-week), got {len(parts)}")
    sets = []
    for text, (name, lo, hi) in zip(parts, _FIELDS):
        try:
            sets.append(_parse_field(text, lo, hi))
        except ValueError as exc:
            msg = str(exc)
            if msg.startswith("invalid literal"):
                msg = f"cannot parse '{text}'"
            raise ValueError(f"{name}: {msg}") from None
    if 7 in sets[4]:
        sets[4] = (sets[4] - {7}) | {0}
    return sets


def cron_errors(expr: Optional[str]) -> List[str]:
    try:
        _parse(expr or "")
    except ValueError as exc:
        return [str(exc)]
    return []


def cron_matches(expr: str, at: datetime) -> bool:
    try:
        minute, hour, dom, month, dow = _parse(expr)
    except ValueError:
        return False
    if at.minute not in minute or at.hour not in hour or at.month not in month:
        return False
    day_ok, wday_ok = at.day in dom, (at.isoweekday() % 7) in dow
    if len(dom) < 31 and len(dow) < 7:
        return day_ok or wday_ok
    return day_ok and wday_ok
