"""Server-side SVG charts (no scripts, no external references)."""
import re
from datetime import timedelta, timezone
from xml.sax.saxutils import escape, quoteattr

from app.reports.milestones import fmt_duration

LANES = ("detection", "analyst", "indicators", "evidence")
LANE_LABELS = {"detection": "Detection", "analyst": "Analyst", "indicators": "Indicators", "evidence": "Evidence"}

_ILLEGAL = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def _xml_safe(s) -> str:
    return _ILLEGAL.sub("", str(s))


_NS = 'xmlns="http://www.w3.org/2000/svg"'
_FONT = 'font-family="IBM Plex Sans, sans-serif"'
_MONO = 'font-family="Roboto Mono, monospace"'
_HATCH = ('<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
          'patternTransform="rotate(45)"><rect width="6" height="6" fill="#f4f4f5"/>'
          '<line x1="0" y1="0" x2="0" y2="6" stroke="#a1a1aa" stroke-width="2"/></pattern></defs>')

_SEGMENTS = (("TTD", "ttd_seconds", "#ea580c"), ("TTC", "ttc_seconds", "#2563eb"), ("TTR", "ttr_seconds", "#16a34a"))
_BOUNDARIES = (("first_seen", "First seen"), ("detected", "Detected"),
               ("contained", "Contained"), ("recovered", "Recovered"))


def _t(x, y, s, size=11, anchor="middle", fill="#27272a", weight="normal", mono=False):
    font = _MONO if mono else _FONT
    return (f'<text x="{x:.1f}" y="{y}" font-size="{size}" text-anchor="{anchor}" fill="{fill}" '
            f'font-weight="{weight}" {font}>{escape(_xml_safe(s))}</text>')


def _fmt_dt(dt):
    if dt is None:
        return "unknown"
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M")


def lifecycle_svg(m: dict) -> str:
    W, H, X0, BW, BY, BH, MIN = 760, 120, 20, 720, 22, 40, 0.08
    out_of_order = set(m.get("out_of_order") or [])
    durs = [m.get(key) for _, key, _ in _SEGMENTS]
    durs = [None if d is None or d < 0 else d for d in durs]
    known = [d for d in durs if d is not None]
    unknown_n = len(durs) - len(known)
    ksum = sum(known)
    raw = []
    for d in durs:
        if d is None:
            raw.append(1 / 3)
        elif ksum > 0:
            raw.append((1 - unknown_n / 3) * d / ksum)
        else:
            raw.append((1 - unknown_n / 3) / len(known))
    # every segment gets at least MIN of the bar; the rest is split proportionally
    fr = [MIN + (1 - MIN * 3) * r for r in raw]
    parts = [f'<svg {_NS} viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">', _HATCH]
    x = float(X0)
    edges = [x]
    for (label, key, color), d, f in zip(_SEGMENTS, durs, fr):
        w = f * BW
        fill = color if d is not None else "url(#hatch)"
        parts.append(f'<rect x="{x:.1f}" y="{BY}" width="{w:.1f}" height="{BH}" fill={quoteattr(fill)} '
                     f'stroke="#ffffff" stroke-width="1"/>')
        if d is not None:
            txt, tfill = f"{label} {fmt_duration(d)}", "#ffffff"
        elif key in out_of_order:
            txt, tfill = f"{label} out of order", "#3f3f46"
        else:
            txt, tfill = f"{label} unknown", "#3f3f46"
        parts.append(_t(x + w / 2, BY + BH / 2 + 4, txt, 12, fill=tfill, weight="bold"))
        x += w
        edges.append(x)
    for i, ((key, name), ex) in enumerate(zip(_BOUNDARIES, edges)):
        anchor = "start" if i == 0 else ("end" if i == len(_BOUNDARIES) - 1 else "middle")
        parts.append(f'<line x1="{ex:.1f}" y1="{BY + BH}" x2="{ex:.1f}" y2="{BY + BH + 6}" stroke="#71717a"/>')
        parts.append(_t(ex, BY + BH + 20, name, 11, anchor, weight="bold"))
        parts.append(_t(ex, BY + BH + 34, _fmt_dt((m.get(key) or {}).get("at")), 10, anchor, fill="#52525b", mono=True))
    parts.append("</svg>")
    return "".join(parts)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def timeline_svg(events: list) -> str:
    W, H = 760, 60 + 44 * 4
    X0, X1, R = 100, 760 - 40, 9  # ~40px right padding keeps the last tick label and markers inside
    PW = X1 - X0
    head = f'<svg {_NS} viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">'
    if not events:
        return head + _t(W / 2, H / 2, "No events", 13, fill="#71717a") + "</svg>"
    evs = sorted(({**e, "at": _aware(e["at"])} for e in events), key=lambda e: (e["at"], e["n"]))
    t0, t1 = evs[0]["at"], evs[-1]["at"]
    span = (t1 - t0).total_seconds()
    if span <= 0:
        t1 = t0 + timedelta(hours=1)
        span = 3600.0

    def xpos(t):
        return min(X0 + (t - t0).total_seconds() / span * PW, W - R - 2)

    top = 20
    parts = [head]
    for i, lane in enumerate(LANES):
        y = top + 44 * i + 22
        parts.append(f'<line x1="{X0}" y1="{y}" x2="{X1}" y2="{y}" stroke="#e4e4e7"/>')
        parts.append(_t(8, y + 4, LANE_LABELS[lane], 11, "start", "#3f3f46", "bold"))
    axis_y = top + 44 * 4 + 8
    parts.append(f'<line x1="{X0}" y1="{axis_y}" x2="{X1}" y2="{axis_y}" stroke="#71717a"/>')
    tick_fmt = "%H:%M:%S" if span < 600 else "%m-%d %H:%M"
    for k in range(5):
        tx = X0 + PW * k / 4
        tt = t0 + (t1 - t0) * k / 4
        parts.append(f'<line x1="{tx:.1f}" y1="{axis_y}" x2="{tx:.1f}" y2="{axis_y + 5}" stroke="#71717a"/>')
        parts.append(_t(tx, axis_y + 18, tt.strftime(tick_fmt), 10,
                        "end" if k == 4 else "middle", fill="#52525b", mono=True))
    by_lane = {lane: [] for lane in LANES}
    for e in evs:
        by_lane[e["lane"] if e["lane"] in by_lane else "analyst"].append(e)
    for i, lane in enumerate(LANES):
        y = top + 44 * i + 22
        groups = []
        for e in by_lane[lane]:
            x = xpos(e["at"])
            if groups and x - groups[-1][0] < 14:
                groups[-1][1].append(e)
            else:
                groups.append((x, [e]))
        for gx, g in groups:
            label = len(g) if len(g) > 1 else g[0]["n"]
            tip = "; ".join(_xml_safe(e.get("text") or "")[:80] for e in g[:3])
            parts.append(f'<circle cx="{gx:.1f}" cy="{y}" r="{R}" fill="#2563eb" stroke="#ffffff">'
                         f'<title>{escape(tip)}</title></circle>')
            parts.append(_t(gx, y + 3.5, label, 9, fill="#ffffff", weight="bold"))
    parts.append("</svg>")
    return "".join(parts)
