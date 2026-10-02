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


def test_timeline_unknown_lane_and_no_unsafe_markup():
    svg = charts.timeline_svg([ev(1, 0, lane="bogus", text="a"), ev(2, 5, lane="detection")])
    root = ET.fromstring(svg)
    forbidden_tags = {"script", "foreignObject", "a", "image", "use"}
    for el in root.iter():
        assert el.tag.replace(NS, "") not in forbidden_tags
        for attr in el.attrib:
            assert not attr.lower().startswith("on") and "href" not in attr.lower()


def test_control_chars_and_none_text():
    svg = charts.timeline_svg([ev(1, 0, text="a\x00b\x0b\ud800c"), ev(2, 5, text=None)])
    ET.fromstring(svg)
    assert "None" not in svg


def test_timeline_same_timestamp_and_naive():
    e1 = {"n": 1, "at": datetime(2026, 10, 1), "lane": "analyst", "text": "x"}
    e2 = {"n": 2, "at": datetime(2026, 10, 1), "lane": "evidence", "text": "y"}
    root = ET.fromstring(charts.timeline_svg([e1, e2]))
    assert len(list(root.iter(NS + "circle"))) == 2
    ET.fromstring(charts.timeline_svg([e1, ev(3, 10)]))


def test_lifecycle_min_segment_width():
    out = ms.compute(ioc_first_seen=[T0 - timedelta(days=30)], alert_created=[], case_created=T0,
                     contained_candidates=[T0 + timedelta(seconds=30)], resolved_at=T0 + timedelta(seconds=90),
                     overrides={})
    root = ET.fromstring(charts.lifecycle_svg(out))
    rects = [float(r.get("width")) for r in root.iter(NS + "rect") if r.get("height") == "40"]
    assert len(rects) == 3 and all(w >= 0.08 * 720 - 0.2 for w in rects)


def test_timeline_last_tick_and_markers_stay_inside():
    import re
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    evs = [{"n": i + 1, "at": t0 + timedelta(hours=i), "lane": "analyst", "kind": "comment", "text": "x",
            "actor": None} for i in range(3)]
    svg = charts.timeline_svg(evs)
    width = int(re.search(r'width="(\d+)"', svg).group(1))
    for cx in re.findall(r'<circle cx="([\d.]+)"', svg):
        assert float(cx) <= width - 9 - 2
    ticks = re.findall(r'<text x="([\d.]+)" y="\d+" font-size="10" text-anchor="(\w+)"[^>]*>(\d\d-\d\d \d\d:\d\d)<', svg)
    assert ticks and ticks[-1][1] == "end"
    assert float(ticks[-1][0]) <= width - 30
