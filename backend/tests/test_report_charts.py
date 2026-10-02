import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from app.reports import charts, milestones as ms

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
NS = "{http://www.w3.org/2000/svg}"


def m(**ov):
    return ms.compute(ioc_first_seen=[T0 - timedelta(days=2)], alert_created=[], case_created=T0,
                      contained_candidates=[T0 + timedelta(hours=6)], resolved_at=T0 + timedelta(days=1), overrides=ov)


def test_lifecycle_valid_and_labels():
    svg = charts.lifecycle_svg(m())
    root = ET.fromstring(svg)
    assert root.tag == NS + "svg" and root.get("viewBox")
    text = " ".join(t.text or "" for t in root.iter(NS + "text"))
    for s in ("TTD 2d 0h", "TTC 6h 0m", "TTR 18h 0m", "First seen", "Detected", "Contained", "Recovered"):
        assert s in text


def test_lifecycle_unknown_is_hatched():
    out = ms.compute(ioc_first_seen=[], alert_created=[], case_created=T0, contained_candidates=[],
                     resolved_at=None, overrides={})
    svg = charts.lifecycle_svg(out)
    ET.fromstring(svg)
    assert 'id="hatch"' in svg and "unknown" in svg


def test_lifecycle_out_of_order_labelled():
    out = ms.compute(ioc_first_seen=[T0 + timedelta(days=1)], alert_created=[], case_created=T0,
                     contained_candidates=[T0 + timedelta(hours=6)], resolved_at=T0 + timedelta(days=1),
                     overrides={})
    assert "ttd_seconds" in out["out_of_order"]
    svg = charts.lifecycle_svg(out)
    root = ET.fromstring(svg)
    text = " ".join(t.text or "" for t in root.iter(NS + "text"))
    assert "TTD out of order" in text and "TTD unknown" not in text
    assert "url(#hatch)" in svg
    assert "TTC 6h 0m" in text


def ev(n, minutes, lane="analyst", text="x"):
    return {"n": n, "at": T0 + timedelta(minutes=minutes), "lane": lane, "text": text}


def test_timeline_empty_single_and_escaping():
    assert "No events" in charts.timeline_svg([])
    ET.fromstring(charts.timeline_svg([ev(1, 0)]))
    svg = charts.timeline_svg([ev(1, 0, text='<script>alert(1)</script> & "q"'), ev(2, 60, "evidence")])
    ET.fromstring(svg)
    assert "<script>" not in svg


def test_timeline_merges_close_markers():
    events = [ev(i + 1, 0) for i in range(200)] + [ev(201, 24 * 60)]
    svg = charts.timeline_svg(events)
    root = ET.fromstring(svg)
    circles = [c for c in root.iter(NS + "circle")]
    assert len(circles) == 2
    texts = [t.text for t in root.iter(NS + "text")]
    assert "200" in texts


def test_timeline_unknown_lane_and_no_unsafe_tags():
    svg = charts.timeline_svg([ev(1, 0, lane="bogus", text="a"), ev(2, 5, lane="detection")])
    ET.fromstring(svg)
    for bad in ("<script", "foreignObject", "href=", " on"):
        assert bad not in svg
