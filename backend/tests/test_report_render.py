import logging
import time
from datetime import datetime, timedelta, timezone

import pytest

import app.reports.pdf as m
from app.core.config import settings
from app.reports.pdf import RenderTimeout, render_html, render_pdf, render_pdf_async

HOSTILE_TITLE = '<img src=http://169.254.169.254/x> Phish <script>alert(1)</script>'
HOSTILE_SUMMARY = ('Line one\n<img src="http://169.254.169.254/latest/meta-data">\n'
                   '<link rel=stylesheet href="https://evil.example/x.css"><script>alert(2)</script>')
T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


def _ms(**over):
    ms = {"first_seen": {"at": T0, "source": "computed"},
          "detected": {"at": T0 + timedelta(hours=5), "source": "computed"},
          "contained": {"at": None, "source": "unknown"},
          "recovered": {"at": None, "source": "unknown"},
          "ttd_seconds": 5 * 3600, "ttc_seconds": None, "ttr_seconds": None, "out_of_order": []}
    ms.update(over)
    return ms


def make_report(*, empty=False, tlp="amber", full=False, **over):
    r = {
        "meta": {"schema": "sochub.incident-report/1", "generated_at": T0 + timedelta(days=2),
                 "generated_by": {"id": 1, "email": "analyst@example.com"}, "app_version": "0.1.0", "full": full},
        "case": {"id": 75, "title": HOSTILE_TITLE, "severity": "high", "status": "open",
                 "owner": "owner@example.com", "created_at": T0 + timedelta(hours=5), "resolved_at": None,
                 "tags": ["phishing", "<b>x</b>"], "sla": {"response": "met", "resolution": "pending"}},
        "report": {"executive_summary": HOSTILE_SUMMARY, "impact": "Mailbox <i>compromise</i>",
                   "lessons_learned": None, "tlp": tlp, "updated_by": "analyst@example.com",
                   "updated_at": T0 + timedelta(days=1)},
        "milestones": _ms(),
        "timeline": [] if empty else [
            {"n": 1, "at": T0 + timedelta(hours=5), "lane": "detection", "kind": "case_created",
             "text": "Case created", "actor": None},
            {"n": 2, "at": T0 + timedelta(hours=6), "lane": "analyst", "kind": "comment",
             "text": '<img src="http://169.254.169.254/tl">', "actor": "analyst@example.com"},
            {"n": 3, "at": T0 + timedelta(hours=7), "lane": "indicators", "kind": "artifact_added",
             "text": "Added http://evil.example/a", "actor": "analyst@example.com"},
        ],
        "iocs": [] if empty else [
            {"value": "https://user:pw@evil.example/login", "type": "url", "tlp": "amber", "threat_level": "high",
             "verdicts": [{"source": "vt", "verdict": "malicious", "score": 90}]},
            {"value": "10.1.2.3", "type": "ip", "tlp": "green", "threat_level": "low", "verdicts": []},
        ],
        "actions": {} if empty else {"containment": [{"title": "Block sender", "completed_at": T0 + timedelta(hours=8),
                                                      "completed_by": "analyst@example.com"}]},
        "outstanding": [] if empty else [{"title": "Reset passwords", "phase": "recovery"}],
        "evidence": [] if empty else [
            {"filename": "mail<1>.eml", "size_bytes": 20480, "sha256": "a" * 64, "is_malicious": True,
             "uploaded_by": "analyst@example.com", "created_at": T0 + timedelta(hours=6)}],
        "truncated": {},
    }
    if full:
        r["audit"] = [{"at": T0, "actor_email": "analyst@example.com", "entity": "case #75", "action": "update"}]
    r.update(over)
    return r


@pytest.fixture
def spy_fetcher(monkeypatch):
    calls = []
    real = m.deny_all_fetcher

    def spy(url, *a, **k):
        calls.append(url)
        if not str(url).startswith("data:"):
            raise ValueError("blocked url")
        return real(url, *a, **k)
    monkeypatch.setattr(m, "deny_all_fetcher", spy)
    return calls


def test_render_pdf_hostile_report_is_pdf_and_never_fetches(spy_fetcher):
    pdf = render_pdf(make_report())
    assert pdf.startswith(b"%PDF")
    assert [u for u in spy_fetcher if not str(u).startswith("data:")] == []


def test_render_html_escapes_and_marks_tlp():
    html = render_html(make_report())
    assert "TLP:AMBER" in html
    assert "&lt;img" in html and "&lt;script&gt;" in html
    assert "<script" not in html and '<img src=http' not in html and '<link rel=stylesheet' not in html
    assert 'class="prose"' in html and "white-space: pre-wrap" in html
    assert "Case #0075" in html


@pytest.mark.parametrize("tlp,label", [("red", "TLP:RED"), ("green", "TLP:GREEN"), ("white", "TLP:CLEAR")])
def test_tlp_labels(tlp, label):
    assert label in render_html(make_report(tlp=tlp))


def test_empty_case_renders(spy_fetcher):
    r = make_report(empty=True, milestones=_ms(first_seen={"at": None, "source": "unknown"}, ttd_seconds=None))
    r["report"].update(executive_summary=None, impact=None)
    assert render_pdf(r).startswith(b"%PDF")
    assert "No events" in render_html(r)


def test_full_report_renders_appendix(spy_fetcher):
    r = make_report(full=True)
    html = render_html(r)
    assert "Audit trail" in html and "case #75" in html
    assert render_pdf(r).startswith(b"%PDF")


def test_iocs_defanged_and_userinfo_stripped():
    html = render_html(make_report())
    assert "hxxps://evil[.]example/login" in html
    assert "user:pw" not in html and "evil.example/login" not in html
    assert "10.1.2[.]3" in html


@pytest.mark.parametrize("value,expected", [
    ("https://user:pw@evil.example/x", "hxxps://evil[.]example/x"),
    ("ftp://user:pw@host.example/f", "ftp://host[.]example/f"),
    ("hxxp://u:p@h.example", "hxxp://h[.]example"),
    ("http://a@b@c.example/p?q=x@y", "hxxp://c[.]example/p?q=x@y"),
    ("//u:p@h.example/x", "//h[.]example/x"),
    ("u:p@h.example/x", "h[.]example/x"),
    ("https://user:p#ss@host.example/x", "hxxps://host[.]example/x"),
    ("https://user:p?ss@host.example/x", "hxxps://host[.]example/x"),
    ("https:/u:p@h.example", "hxxps:/h[.]example"),
    ("https:\\\\u:p@h.example", "hxxps:\\\\h[.]example"),
    ("  https://u:p@h.example/x", "hxxps://h[.]example/x"),
    ("https://h.example/p@x", "hxxps://h[.]example/p@x"),
])
def test_defang_filter_strips_url_userinfo(value, expected):
    assert m._defang_filter(value, "url") == expected


def test_defang_filter_strips_userinfo_when_query_has_scheme():
    out = m._defang_filter("u:p@h.example/r?x=http://y", "url")
    assert "u:p" not in out and out.startswith("h")


def test_defang_filter_leaves_email_at():
    assert m._defang_filter("bob@evil.example", "email") == "bob[@]evil[.]example"


def test_truncation_slices_and_notes():
    r = make_report()
    r["iocs"] = [{"value": f"{i}.example.com", "type": "domain", "tlp": "white", "threat_level": None,
                  "verdicts": []} for i in range(1003)]
    html = render_html(r)
    assert "+3 more — see JSON export" in html
    assert "1002[.]example[.]com" not in html and "999[.]example[.]com" in html


def test_out_of_order_note():
    r = make_report(milestones=_ms(ttc_seconds=None, out_of_order=["ttc_seconds"]))
    assert "Milestone times are out of order — check the overrides." in render_html(r)
    assert "Milestone times are out of order" not in render_html(make_report())


def test_no_case_text_or_urls_in_logs(caplog, spy_fetcher):
    with caplog.at_level(logging.DEBUG):
        render_pdf(make_report())
    blob = "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records)
    for needle in ("169.254.169.254", "evil.example", "Phish", "Line one", "alert(", "compromise"):
        assert needle not in blob


def _pdf_font_names(pdf: bytes) -> set:
    import re
    import zlib
    parts = [pdf]
    for x in re.finditer(rb"stream\r?\n", pdf):
        try:
            parts.append(zlib.decompress(pdf[x.end():pdf.find(b"endstream", x.end())]))
        except zlib.error:
            pass
    return {f.split(b"+", 1)[-1].decode() for f in re.findall(rb"/BaseFont\s*/([A-Za-z0-9+_-]+)", b"".join(parts))}


def test_embedded_fonts_are_used():
    html = render_html(make_report())
    assert "&#39;" not in html.split("</style>")[0]  # CSS must not be HTML-escaped (raw-text element)
    names = _pdf_font_names(render_pdf(make_report()))
    assert "IBM-Plex-Sans" in names and "Roboto-Mono" in names
    assert not any("DejaVu" in n for n in names)


def test_blocked_fetch_is_not_logged(caplog, spy_fetcher):
    # A fetch that IS attempted and denied: WeasyPrint would log the URL at ERROR unless silenced (R3).
    with caplog.at_level(logging.DEBUG):
        m.html_to_pdf('<p>x</p><img src="http://169.254.169.254/leak"><link rel=stylesheet href="https://evil.example/s.css">')
    assert spy_fetcher  # the attempt happened
    blob = "\n".join(f"{r.name} {r.getMessage()} {r.args}" for r in caplog.records)
    assert "169.254.169.254" not in blob and "evil.example" not in blob


@pytest.mark.asyncio
async def test_render_pdf_async_timeout_then_ok(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_RENDER_TIMEOUT_SECONDS", 1)
    real = m.render_pdf
    monkeypatch.setattr(m, "render_pdf", lambda report: time.sleep(2))
    with pytest.raises(RenderTimeout):
        await render_pdf_async(make_report())
    monkeypatch.setattr(m, "render_pdf", real)
    monkeypatch.setattr(settings, "REPORT_RENDER_TIMEOUT_SECONDS", 60)
    pdf = await render_pdf_async(make_report())
    assert pdf.startswith(b"%PDF")


def test_timeline_table_cells_have_no_utc_suffix():
    html = render_html(make_report())
    assert "Time (UTC)" in html
    assert '<td class="mono">2026-09-01 13:00</td>' in html
    assert '<td class="mono">2026-09-01 13:00 UTC</td>' not in html


@pytest.mark.asyncio
async def test_render_slot_held_until_thread_finishes(monkeypatch):
    import asyncio
    import threading
    monkeypatch.setattr(settings, "REPORT_RENDER_TIMEOUT_SECONDS", 1)
    lock = threading.Lock()
    state = {"active": 0, "max": 0, "calls": 0}
    durations = [1.5, 0.1]

    def fake(report):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            d = durations[state["calls"]]
            state["calls"] += 1
        time.sleep(d)
        with lock:
            state["active"] -= 1
        return b"%PDF-fake"
    monkeypatch.setattr(m, "render_pdf", fake)
    loop = asyncio.get_running_loop()
    with pytest.raises(RenderTimeout):
        await render_pdf_async({})  # times out at ~1s; its thread keeps the slot until ~1.5s
    assert state["active"] == 1  # thread still running after the caller gave up
    t = loop.time()
    assert await render_pdf_async({}) == b"%PDF-fake"
    assert loop.time() - t >= 0.3  # waited for the first thread to free the slot
    assert state["max"] == 1 and state["calls"] == 2


@pytest.mark.asyncio
async def test_slot_wait_times_out(monkeypatch):
    import asyncio
    monkeypatch.setattr(settings, "REPORT_RENDER_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(m, "render_pdf", lambda report: time.sleep(2.2) or b"%PDF")
    first = asyncio.ensure_future(render_pdf_async({}))
    await asyncio.sleep(0.05)
    with pytest.raises(RenderTimeout):
        await render_pdf_async({})  # slot still held by the first render's thread
    with pytest.raises(RenderTimeout):
        await first
    await asyncio.sleep(1.3)  # let the thread finish within this loop
