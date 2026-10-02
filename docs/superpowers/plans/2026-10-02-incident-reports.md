# Incident Report Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each case, analysts write a report (summary, impact, lessons learned, milestone overrides, TLP). They export it as a curated PDF with a lifecycle chart and a swimlane timeline chart, or as JSON. "Draft with AI" helps write the text. Every export is audited with its SHA-256.

**Architecture:**
- **Pure modules** in `app/reports/`: `tlp.py` (floor, labels, colours), `defang.py`, `milestones.py`, `charts.py` (SVG).
- **`builder.py`** reads the DB and produces one plain report dict.
- **`pdf.py`** renders that dict to PDF with a Jinja2 template and WeasyPrint. A deny-all URL fetcher blocks network access, fonts are embedded, and there is a semaphore and a timeout.
- **The API** lives in `app/api/v1/reports.py`.
- **The frontend** adds a Report tab.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Jinja2 (installed), WeasyPrint (new), React 19 + TanStack Query.

**Spec:** `docs/superpowers/specs/2026-10-02-incident-reports-design.md`

## Global Constraints

- **Branch:** `feat/incident-reports`, stacked on `fix/layout-spacing`.
- **Migration:** `b6c7d8e9f0a1` with `down_revision = "a5b6c7d8e9f0"`. The live DB is at `a5b6c7d8e9f0`.
- **TLP:**
  - Order: `white` < `green` < `amber` < `red`.
  - Labels: white→`CLEAR`, green→`GREEN`, amber→`AMBER`, red→`RED`.
  - Background colours: RED `#FF2B2B`, AMBER `#FFC000`, GREEN `#33FF00`, CLEAR `#FFFFFF`.
  - Text colours: white on RED, black on all others. CLEAR also gets a black border.
- **Text limits:** narrative fields are at most 20,000 characters each.
- **PDF row limits:** timeline 500, IOCs 1000, evidence 1000, audit 2000. JSON is never truncated.
- **PDF rendering:**
  - Runs in `asyncio.to_thread`.
  - A module-level `asyncio.Semaphore(1)` allows one render at a time.
  - Timeout is `REPORT_RENDER_TIMEOUT_SECONDS` (setting, default 60). On timeout return 504 `"Report rendering timed out"`.
- **URL fetcher:** WeasyPrint's fetcher allows only `data:` URLs. Anything else raises `ValueError`.
- **JSON schema id:** `"sochub.incident-report/1"`.
- **Export filename:** `case-{id:04d}-report-TLP-{LABEL}-{YYYYMMDD}.{pdf|json}`, using the UTC date.
- **Roles:**
  - GET report and charts: any member (`get_current_active_user`).
  - PUT, draft and export: `require_analyst_or_above`.
  - Cross-tenant case: 404.
- **Audit entries:**
  - PUT: `entity_type="case_report"`, `entity_id=case_id`, `action="update"`, `changes={"fields": [...]}`.
  - Export: `entity_type="case"`, `entity_id=case_id`, `action="export_report"`, `changes={"format","full","tlp","sha256","size_bytes"}`.
- **Logging:** exactly one INFO line per op from logger `app.api.v1.reports`: `report <op> tenant=%s case=%s format=%s full=%s outcome=%s`. Never log narrative text or IOC values.
- **SLA:** use `compute_sla_state(case, await load_policy_overrides(db, tenant_id))` from `app/utils/sla.py` exactly as `app/api/v1/cases.py::_attach_sla` does. Report `response_status` and `resolution_status` verbatim. (Ruling: these are the existing values `on_track`/`at_risk`/`breached`/…, not the spec's `met`/`breached`/`pending`.)
- **Tests:**
  - No network and no real AI.
  - Temporary DB rows are deleted by exact id. Call `engine.dispose()` at entry and exit.
  - The `wf-test-%` tenant count must be 0 after the suite.
  - The existing `tests/conftest.py` blocks enrichment enqueue.
- **Commands:**
  - Tests: `docker compose exec -T backend python -m pytest tests/<file> -q`.
  - After backend changes: `docker restart case_management-backend-1 case_management-worker-1`.
  - After Dockerfile or requirements changes: `docker compose build backend worker && docker compose up -d --no-deps backend worker`.
- **Commits:** `perl -e 'alarm 60; exec @ARGV' git commit ...`, with the agent's own Co-Authored-By line. If signing fails, leave the changes staged and report it.

## Review Focus

1. **Hostile case content.** A case title, IOC or narrative containing `<img src=http://169.254.169.254/x>`, `<script>` or `&` must render escaped. It must never cause a fetch, and the SVG must still be valid. → Tests in Tasks 3 and 4.
2. **Edge-case cases.** A case with no IOCs, no tasks, no timeline and no resolution must render a valid PDF and SVGs, showing "unknown" milestones and "No events". → Tasks 3, 4 and 6.
3. **TLP floor bypass.** The export `tlp` query parameter, or a PUT, must never produce a report below the IOC floor. → Tasks 2 and 6.
4. **Clustered timestamps.** Hundreds of events in the same minute must merge into one marker, not draw hundreds of overlapping circles. → Task 3.
5. **Hung renders.** A very slow render must not hang the server: the semaphore and timeout return 504, and the next request still works. → Task 6.

---

### Task 1: WeasyPrint runtime, fonts, URL fetcher and settings

**Files:**
- Modify: `backend/Dockerfile`, `backend/requirements.txt`, `backend/app/core/config.py`
- Create:
  - `backend/app/reports/__init__.py` (empty)
  - `backend/app/reports/fonts/` containing these OFL font files plus their `OFL.txt` licences:
    - `IBMPlexSans-Regular.ttf`
    - `IBMPlexSans-SemiBold.ttf`
    - `RobotoMono-Regular.ttf`
  - `backend/app/reports/pdf.py`, with `deny_all_fetcher`, `font_face_css()` and a minimal `html_to_pdf(html) -> bytes`
- Test: `backend/tests/test_report_pdf_basics.py`

**Interfaces:**
- Produces:
  - `deny_all_fetcher(url, timeout=10, ssl_context=None)`: allows `data:` URLs, raises `ValueError("blocked url")` for everything else.
  - `font_face_css() -> str`: `@font-face` rules with base64 `data:` URLs, cached.
  - `html_to_pdf(html: str) -> bytes`
  - Setting `REPORT_RENDER_TIMEOUT_SECONDS: int = Field(60, ge=5, le=600)`.

- [ ] **Step 1: Fonts.**
  - Download the three TTFs and their OFL licence files from the official upstream repos:
    - IBM Plex: `github.com/IBM/plex`, release asset `plex-sans`.
    - Roboto Mono: `github.com/googlefonts/RobotoMono`.
  - Commit them under `backend/app/reports/fonts/`.
  - Record the source URLs in `backend/app/reports/fonts/SOURCES.md`.
  - If the sandbox blocks downloads, copy the fonts from the frontend's dependencies if they're present. Otherwise report NEEDS_CONTEXT; do not invent font files.
- [ ] **Step 2: Dockerfile.**
  - In the runtime stage, before the pip install, add:
    ```dockerfile
    RUN apt-get update \
     && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 shared-mime-info \
     && rm -rf /var/lib/apt/lists/*
    ```
  - Check against the WeasyPrint docs for the version you pin. Use the "Debian ≥ 11" install line; if the package names differ, follow the docs.
  - Append `weasyprint` to `requirements.txt`.
  - Rebuild, then read the installed version and pin it exactly.
  - Rebuild again.
  - Run `pip-audit -r requirements.txt` and expect no known vulnerabilities.
  - Run `docker compose exec -T backend python -c "import weasyprint; print(weasyprint.__version__)"`.
- [ ] **Step 3: Failing tests** in `backend/tests/test_report_pdf_basics.py`:

```python
import pytest

from app.reports.pdf import deny_all_fetcher, font_face_css, html_to_pdf


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest", "https://example.com/x.png",
                                 "file:///etc/passwd", "relative.png", "//evil.com/x"])
def test_fetcher_blocks_everything_but_data(url):
    with pytest.raises(ValueError):
        deny_all_fetcher(url)


def test_fetcher_allows_data():
    r = deny_all_fetcher("data:text/plain;base64,aGk=")
    body = r.get("string") or r.get("file_obj").read()
    assert body == b"hi"


def test_font_css_embeds_fonts_as_data_urls():
    css = font_face_css()
    assert css.count("@font-face") == 3 and "url(data:font/ttf;base64," in css and "http" not in css


def test_html_to_pdf_minimal_and_no_fetch(monkeypatch):
    calls = []
    import app.reports.pdf as m
    real = m.deny_all_fetcher

    def spy(url, *a, **k):
        calls.append(url)
        return real(url, *a, **k)
    monkeypatch.setattr(m, "deny_all_fetcher", spy)
    pdf = html_to_pdf('<html><body><p>hi</p><img src="http://169.254.169.254/x"></body></html>')
    assert pdf.startswith(b"%PDF")
    assert all(not u.startswith("data:") for u in calls)  # only the blocked attempt, if any
```

- [ ] **Step 4: Implement** `backend/app/reports/pdf.py`:

```python
"""PDF rendering primitives: no network, embedded fonts."""
import base64
from functools import lru_cache
from pathlib import Path

from weasyprint import HTML, default_url_fetcher

FONTS = Path(__file__).parent / "fonts"
_FACES = [("IBM Plex Sans", "IBMPlexSans-Regular.ttf", 400), ("IBM Plex Sans", "IBMPlexSans-SemiBold.ttf", 600),
          ("Roboto Mono", "RobotoMono-Regular.ttf", 400)]


def deny_all_fetcher(url, timeout=10, ssl_context=None):
    """Only inline data: URLs. Case content must never make the server fetch anything."""
    if isinstance(url, str) and url.startswith("data:"):
        return default_url_fetcher(url, timeout=timeout, ssl_context=ssl_context)
    raise ValueError("blocked url")


@lru_cache(maxsize=1)
def font_face_css() -> str:
    out = []
    for family, file, weight in _FACES:
        b64 = base64.b64encode((FONTS / file).read_bytes()).decode()
        out.append(f"@font-face{{font-family:'{family}';font-weight:{weight};"
                   f"src:url(data:font/ttf;base64,{b64}) format('truetype');}}")
    return "\n".join(out)


def html_to_pdf(html: str) -> bytes:
    return HTML(string=html, url_fetcher=lambda u, *a, **k: deny_all_fetcher(u, *a, **k), base_url=None).write_pdf()
```

The `lambda` above looks `deny_all_fetcher` up at call time, so the spy test works. Check `default_url_fetcher`'s signature for the pinned WeasyPrint version and adjust the passthrough if its keywords differ.

Add the setting to `config.py`, next to `MAX_UPLOAD_MB`.

- [ ] **Step 5: Run** the tests (PASS), then the full suite. Restart the containers.
- [ ] **Step 6: Commit**

```bash
git add backend/Dockerfile backend/requirements.txt backend/app/core/config.py backend/app/reports backend/tests/test_report_pdf_basics.py
git commit -m "feat(reports): WeasyPrint runtime, embedded fonts and no-network fetcher"
```

---

### Task 2: Model, migration and pure helpers (TLP, defang, milestones)

**Files:**
- Create:
  - `backend/app/models/case_report.py`
  - `backend/alembic/versions/b6c7d8e9f0a1_case_reports.py`
  - `backend/app/reports/tlp.py`
  - `backend/app/reports/defang.py`
  - `backend/app/reports/milestones.py`
- Modify: `backend/app/db/base.py` (register the model)
- Test: `backend/tests/test_report_helpers.py`

**Interfaces:**
- **Model `CaseReport`:** columns as in the spec. `tlp` defaults to `"amber"`, with server default `'amber'`.
- **`tlp.py`:**
  - `ORDER = ("white","green","amber","red")`
  - `LABEL`
  - `COLORS: dict[str, tuple[bg, fg]]`
  - `def floor(tlps: Iterable[str|None]) -> str`: the max of the valid values; `"white"` if there are none.
  - `def at_least(tlp: str, minimum: str) -> bool`
- **`defang.py`:** `def defang(value: str, itype: str) -> str`, where `itype` is a raw IOC or artifact type such as `ip_address`/`ip`, `domain`, `url`, `email`, `file_hash` or other.
- **`milestones.py`:** `def compute(*, ioc_first_seen: list[datetime], alert_created: list[datetime], case_created: datetime, contained_candidates: list[datetime], resolved_at: datetime|None, overrides: dict[str, datetime|None]) -> dict`. It returns:

  ```
  {"first_seen": {"at": dt|None, "source": "override"|"computed"|"unknown"},
   "detected": …, "contained": …, "recovered": …,
   "ttd_seconds": int|None, "ttc_seconds": int|None, "ttr_seconds": int|None}
  ```

  - `def ordered(overrides: dict) -> bool`: True when the non-null overrides satisfy first_seen ≤ detected ≤ contained ≤ recovered.
  - `def fmt_duration(seconds: int|None) -> str`: returns `"unknown"`, `"45m"`, `"6h 12m"` or `"2d 4h"`.

- [ ] **Step 1: Model and migration.**
  - The migration creates `case_reports` with a unique `case_id`, CASCADE FKs, and an index on `tenant_id`.
  - Check `alembic upgrade head`, then `downgrade -1`, then `upgrade head`, and confirm the head is `b6c7d8e9f0a1`.
- [ ] **Step 2: Failing tests** in `backend/tests/test_report_helpers.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from app.reports import defang as d, milestones as ms, tlp

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_tlp_floor_and_at_least():
    assert tlp.floor([]) == "white" and tlp.floor([None, "bogus"]) == "white"
    assert tlp.floor(["green", "red", "amber"]) == "red"
    assert tlp.at_least("amber", "green") and not tlp.at_least("green", "amber")
    assert tlp.LABEL["white"] == "CLEAR" and tlp.COLORS["red"] == ("#FF2B2B", "#FFFFFF")


@pytest.mark.parametrize("value,itype,want", [
    ("http://evil.com/a.b", "url", "hxxp://evil[.]com/a.b"),
    ("https://x.y.com", "url", "hxxps://x[.]y[.]com"),
    ("evil.co.uk", "domain", "evil[.]co[.]uk"),
    ("1.2.3.4", "ip_address", "1.2.3[.]4"),
    ("2001:db8::1", "ip", "2001[:]db8[:][:]1"),
    ("a.b@evil.com", "email", "a.b[@]evil[.]com"),
    ("d41d8cd98f00b204e9800998ecf8427e", "file_hash", "d41d8cd98f00b204e9800998ecf8427e"),
    ("HKLM\\Run", "registry_key", "HKLM\\Run"),
])
def test_defang(value, itype, want):
    assert d.defang(value, itype) == want


def test_milestones_computed_override_unknown():
    out = ms.compute(ioc_first_seen=[T0 - timedelta(days=2)], alert_created=[T0 - timedelta(days=1)],
                     case_created=T0, contained_candidates=[T0 + timedelta(hours=3), T0 + timedelta(hours=6)],
                     resolved_at=None, overrides={"recovered": None})
    assert out["first_seen"] == {"at": T0 - timedelta(days=2), "source": "computed"}
    assert out["detected"]["source"] == "computed" and out["contained"]["at"] == T0 + timedelta(hours=6)
    assert out["recovered"] == {"at": None, "source": "unknown"}
    assert out["ttd_seconds"] == 2 * 86400 and out["ttc_seconds"] == 6 * 3600 and out["ttr_seconds"] is None
    o2 = ms.compute(ioc_first_seen=[], alert_created=[], case_created=T0, contained_candidates=[],
                    resolved_at=T0 + timedelta(days=1), overrides={"first_seen": T0 - timedelta(hours=1)})
    assert o2["first_seen"]["source"] == "override" and o2["contained"]["source"] == "unknown"
    assert o2["ttd_seconds"] == 3600 and o2["ttc_seconds"] is None and o2["ttr_seconds"] is None


def test_ordered_and_fmt():
    assert ms.ordered({"first_seen": T0, "detected": T0 + timedelta(1), "contained": None, "recovered": T0 + timedelta(3)})
    assert not ms.ordered({"detected": T0 + timedelta(1), "contained": T0})
    assert [ms.fmt_duration(x) for x in (None, 2700, 22320, 187200)] == ["unknown", "45m", "6h 12m", "2d 4h"]
```

- [ ] **Step 3: Implement.**

`tlp.py`:

```python
ORDER = ("white", "green", "amber", "red")
LABEL = {"white": "CLEAR", "green": "GREEN", "amber": "AMBER", "red": "RED"}
COLORS = {"white": ("#FFFFFF", "#000000"), "green": ("#33FF00", "#000000"),
          "amber": ("#FFC000", "#000000"), "red": ("#FF2B2B", "#FFFFFF")}


def floor(tlps) -> str:
    vals = [t for t in tlps if t in ORDER]
    return max(vals, key=ORDER.index) if vals else "white"


def at_least(t: str, minimum: str) -> bool:
    return t in ORDER and ORDER.index(t) >= ORDER.index(minimum)
```

`defang.py`:

```python
import re


def defang(value: str, itype: str) -> str:
    t = (itype or "").lower()
    v = value or ""
    if t == "url":
        v = re.sub(r"^https", "hxxps", v, flags=re.I) if v.lower().startswith("https") else re.sub(r"^http", "hxxp", v, flags=re.I)
        scheme, sep, rest = v.partition("://")
        host, slash, path = rest.partition("/")
        return f"{scheme}{sep}{host.replace('.', '[.]')}{slash}{path}" if sep else v.replace(".", "[.]")
    if t == "domain":
        return v.replace(".", "[.]")
    if t in ("ip", "ip_address"):
        if ":" in v:
            return v.replace(":", "[:]")
        head, dot, tail = v.rpartition(".")
        return f"{head}[.]{tail}" if dot else v
    if t == "email":
        local, at, dom = v.partition("@")
        return f"{local}[@]{dom.replace('.', '[.]')}" if at else v
    return v
```

`milestones.py`:

```python
from datetime import datetime
from typing import Dict, List, Optional

KEYS = ("first_seen", "detected", "contained", "recovered")


def _pick(override, computed):
    if override is not None:
        return {"at": override, "source": "override"}
    if computed is not None:
        return {"at": computed, "source": "computed"}
    return {"at": None, "source": "unknown"}


def _secs(a, b):
    return int((b - a).total_seconds()) if a is not None and b is not None else None


def compute(*, ioc_first_seen: List[datetime], alert_created: List[datetime], case_created: datetime,
            contained_candidates: List[datetime], resolved_at: Optional[datetime], overrides: Dict) -> Dict:
    early = [x for x in (*ioc_first_seen, *alert_created) if x is not None]
    out = {
        "first_seen": _pick(overrides.get("first_seen"), min(early) if early else None),
        "detected": _pick(overrides.get("detected"), case_created),
        "contained": _pick(overrides.get("contained"), max(contained_candidates) if contained_candidates else None),
        "recovered": _pick(overrides.get("recovered"), resolved_at),
    }
    at = {k: out[k]["at"] for k in KEYS}
    out["ttd_seconds"] = _secs(at["first_seen"], at["detected"])
    out["ttc_seconds"] = _secs(at["detected"], at["contained"])
    out["ttr_seconds"] = _secs(at["contained"], at["recovered"])
    return out


def ordered(overrides: Dict) -> bool:
    seq = [overrides.get(k) for k in KEYS if overrides.get(k) is not None]
    return all(a <= b for a, b in zip(seq, seq[1:]))


def fmt_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "unknown"
    m = max(0, seconds) // 60
    d, h, mm = m // 1440, (m % 1440) // 60, m % 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {mm}m"
    return f"{mm}m"
```

Note: the `"2001:db8::1"` defang case expects `2001[:]db8[:][:]1`, which is what `.replace(":", "[:]")` produces.

- [ ] **Step 4: Run** the tests (PASS), then the full suite.
- [ ] **Step 5: Commit**

```bash
git add backend/app/models/case_report.py backend/app/db/base.py backend/alembic/versions/b6c7d8e9f0a1_case_reports.py \
  backend/app/reports/tlp.py backend/app/reports/defang.py backend/app/reports/milestones.py backend/tests/test_report_helpers.py
git commit -m "feat(reports): case_reports table, TLP floor, defang and milestone helpers"
```

---

### Task 3: SVG charts

**Files:**
- Create: `backend/app/reports/charts.py`
- Test: `backend/tests/test_report_charts.py`

**Interfaces:**
- Consumes: `milestones.fmt_duration`.
- Produces:
  - `def lifecycle_svg(m: dict) -> str`, where `m` is the dict returned by `milestones.compute`.
  - `def timeline_svg(events: list[dict]) -> str`, where each event is `{n:int, at:datetime, lane:str, text:str}`.
  - `LANES = ("detection","analyst","indicators","evidence")` with labels `Detection`, `Analyst`, `Indicators`, `Evidence`.

- [ ] **Step 1: Failing tests** in `backend/tests/test_report_charts.py`:

```python
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
```

- [ ] **Step 2: Implement** `charts.py`.
  - Build SVG strings with `xml.sax.saxutils.escape` / `quoteattr` for every dynamic value.
  - Use `xmlns="http://www.w3.org/2000/svg"` and a `viewBox`, with no external references.
  - **Lifecycle chart:**
    - Width 760 and height 120.
    - Three segments: dwell (`#ea580c`), response (`#2563eb`) and recovery (`#16a34a`).
    - Segment widths are proportional to their durations, with each at least 8% of the bar. When a duration is unknown, give it an equal share and fill it with `url(#hatch)`. The `<pattern id="hatch">` uses diagonal zinc lines and is always defined.
    - Labels: `TTD <fmt>`, `TTC <fmt>` and `TTR <fmt>` centred on their segments. Under each of the 4 boundaries, show the milestone name and its date (`%Y-%m-%d %H:%M` UTC, or `unknown`).
  - **Timeline chart:**
    - Width 760 and height `60 + 44*4`, with lane labels in the left 90px gutter.
    - The axis runs from the first event to the last. Use 1 hour if they are equal.
    - Draw 5 ticks labelled `%m-%d %H:%M`.
    - Place each event at `x = 100 + (t - t0)/(t1 - t0) * 640`, on its lane's y.
    - Merging: per lane, walk events in time order. If an event's x is within 14px of the current group's first x, it joins the group. Each group draws one circle (r=9) labelled with its count when the group has more than one event, otherwise with its `n`.
    - Unknown lanes map to `analyst`.
- [ ] **Step 3: Run** the tests (PASS).
- [ ] **Step 4: Commit:** `feat(reports): server-side SVG lifecycle and swimlane timeline charts`

---

### Task 4: Report builder

**Files:**
- Create: `backend/app/reports/builder.py`
- Test: `backend/tests/test_report_builder.py`

**Interfaces:**
- Consumes:
  - `CaseReport`, `milestones.compute`, `tlp.floor`.
  - The models `Case`, `TimelineEvent`, `Alert` (`Alert.case_id`, `created_at`), `IOC` (`case_id`, `tlp`, `first_seen`, `threat_level`, `value`, `ioc_type`), `CaseArtifact` + `Artifact`, `CaseTask` (`phase`, `status`, `completed_at`, `completed_by`, `title`), `CaseAttachment` and `EnrichmentResult`.
  - `app.enrichment.indicators.normalise`.
  - `app.utils.sla.compute_sla_state` and `load_policy_overrides`; find the import path in `app/api/v1/cases.py`.
  - `AuditLog`.
- Produces:
  - `async def build_report(db, case, *, full: bool, generated_by) -> dict`, shaped as in the spec "Report model" section.
  - `async def min_tlp(db, case) -> str`.
  - `async def report_row(db, case) -> CaseReport | None`.

Rules:
- **Timeline lane mapping:**

  | Source | Lane |
  |---|---|
  | `TimelineEvent.event_type` `comment`, `status_change`, `severity_change` | `analyst` |
  | `artifact_added`, `artifact_removed` | `indicators` |
  | `attachment_added`, `attachment_deleted` | `evidence` |
  | case created (synthetic, at `case.created_at`) | `detection` |
  | each linked `Alert.created_at` | `detection` |
  | each completed task (synthetic) | `analyst` |
  | each `EnrichmentResult` with verdict `malicious`, for case IOCs/artifacts (at `fetched_at`) | `indicators` |
  | any other event type | `analyst` |

- **Key mode** (`full=False`) drops `artifact_removed` and `attachment_deleted`. Full mode keeps everything. Sort by time, then number `n` from 1.
- **`text` for each timeline entry:**
  - for timeline events, the event content truncated to 300 characters;
  - for synthetic events, "Case created", "Alert received: {title}", "Task completed: {title}" or "{source} flagged {type} as malicious".
  - Never include the full value for synthetic enrichment events, only the type, so lookups never echo hidden values. The IOC table already shows values.
- **`iocs`:**
  - Case IOCs, plus artifacts linked to the case whose `normalise(type, value)` is not None. Deduplicate by (normalised type, value).
  - `verdicts` comes from `EnrichmentResult` with `status == "ok"` for the tenant and that normalised pair.
  - Sort by: any malicious verdict first, then `threat_level` rank (critical > high > medium > low > info), then value.
- **`evidence`:** attachments with `deleted_at` null, newest first.
- **`audit`** (`full` only): `AuditLog` rows where `tenant_id` matches and either (`entity_type == "case"` and `entity_id == case.id`) or (`entity_type == "attachment"` and `entity_id` is in the case's attachment ids, including deleted ones). Each row is `{at, actor_email, entity, action}`. Sort by time.
- **`truncated`:** compute it but don't slice here. Slicing is the PDF renderer's job.
- **Datetimes** are returned as `datetime` objects. A helper `to_jsonable(report)` converts them to ISO strings for the JSON export.

- [ ] **Step 1: Failing test.**
  - Seed a temporary tenant, case, user, 2 IOCs (one `tlp=red`), one artifact, 3 timeline events (`comment`, `artifact_added`, `attachment_deleted`), one alert linked to the case, two tasks (one done in `containment`), one live attachment and one deleted attachment, an `EnrichmentResult(ok, malicious)` for one IOC, and two audit rows.
  - Assert on each of these:
    - `min_tlp` is `"red"`.
    - Key mode excludes `attachment_deleted` and full mode includes it.
    - The lanes are mapped as in the table.
    - `n` is consecutive.
    - The malicious IOC sorts first.
    - Evidence excludes the deleted attachment.
    - `audit` is present only when `full`.
    - `milestones.contained` equals the task's `completed_at`.
    - A hostile title `<img src=x>` is kept raw in the dict; escaping happens at render time.
  - Clean up by exact ids.
- [ ] **Step 2: Implement and run** the test (PASS), then the full suite.
- [ ] **Step 3: Commit:** `feat(reports): incident report builder`

---

### Task 5: PDF template and renderer

**Files:**
- Create: `backend/app/reports/templates/report.html.j2`
- Modify: `backend/app/reports/pdf.py`, adding `render_pdf(report: dict) -> bytes`, `async def render_pdf_async(report) -> bytes` (semaphore + timeout) and `class RenderTimeout(Exception)`.
- Test: `backend/tests/test_report_render.py`

Rules:
- **Jinja environment:** `Environment(loader=FileSystemLoader(templates), autoescape=True, undefined=StrictUndefined)`.
- **Template filters:** `defang` (value, type), `dt` (UTC `%Y-%m-%d %H:%M UTC`), `duration` (`fmt_duration`), `filesize`.
- **Charts** are inserted with `{{ lifecycle_svg | safe }}` and `{{ timeline_svg | safe }}`. They are the only `safe` values, because the chart module escapes its own input.
- **Narrative:** `<div class="prose">{{ report.report.executive_summary or "—" }}</div>` with `white-space: pre-wrap`.
- **Page CSS:** `@page` A4 with margins 18mm/14mm. The `@top-center` and `@bottom-center` content is `"TLP:" LABEL`, using the TLP colours. `@bottom-right` shows `"Case #0075 · page " counter(page) " of " counter(pages)`.
- **Sections**, in the order the spec lists. Slice each table to the PDF row limits and print "+N more — see JSON export" when a table is truncated.
- **Fonts:** prepend `font_face_css()` in a `<style>` block. The body uses `'IBM Plex Sans'` and data uses `'Roboto Mono'`.
- **`render_pdf_async`:**

  ```python
  async with _SEM:
      try:
          return await asyncio.wait_for(asyncio.to_thread(render_pdf, report), timeout)
      except asyncio.TimeoutError:
          raise RenderTimeout()
  ```

  `timeout` is read from settings at call time. The thread keeps running after a timeout; that is the documented limitation.

- [ ] **Step 1: Failing tests.**
  - Build a minimal report dict by hand (no DB), with a hostile title and summary (`<img src=http://169.254.169.254/x>`, `<script>`).
  - Then check:
    - `render_pdf(report).startswith(b"%PDF")`;
    - the spied fetcher never receives a non-`data:` URL (if it does, it raises, and the test asserts that no such URL was attempted);
    - for TLP text: render the HTML only (`render_html(report)`) and assert that `TLP:AMBER` appears and that `&lt;img` appears escaped;
    - the empty-case report (no IOCs, no events) renders to a PDF;
    - `render_pdf_async` with `render_pdf` monkeypatched to `time.sleep(2)` and the timeout setting at 1 raises `RenderTimeout`, and a following normal call succeeds.
- [ ] **Step 2: Implement and run** (PASS).
- [ ] **Step 3: Commit:** `feat(reports): PDF template with TLP marking, charts and safe rendering`

---

### Task 6: Reports API

**Files:**
- Create: `backend/app/schemas/report.py`, `backend/app/api/v1/reports.py`
- Modify: `backend/app/api/api.py` (`include_router(reports.router, prefix="/cases", tags=["reports"])`)
- Test: `backend/tests/test_reports_api.py`

Behaviour is exactly the spec's API table plus the Global Constraints. Details:

- **`ReportIn`:**
  - Three `Optional[str] = Field(None, max_length=20000)` text fields.
  - `tlp: Literal["white","green","amber","red"] = "amber"`.
  - Four `Optional[datetime]` overrides, which must be timezone-aware. A naive datetime is rejected with 422.
- **`GET`:** returns `{report: {...fields, updated_by_email, updated_at}, milestones, min_tlp, ai_available}`. `ai_available` comes from `describe(db, tenant_id)["provider"] is not None`.
- **Draft:**
  - The system prompt asks for JSON `{"executive_summary","impact","lessons_learned"}`: plain text, no markdown, written for management, and factual only from the provided data.
  - Parse the reply with the existing `_parse_json_reply` helper from `app/services/ai_service.py` (import it), and coerce each field to `str[:20000]`.
  - `AIError` gives 503 `"AI drafting is unavailable"`.
- **Export:**
  - Call `build_report`. For PDF, call `render_pdf_async` and map `RenderTimeout` to 504. For JSON, use `json.dumps(to_jsonable(report), ensure_ascii=False, indent=2).encode()`.
  - The export TLP is `max(tlp query or report.tlp, floor)`. If the query parameter is explicitly below the floor, return 422 `"TLP must be at least {LABEL}"`.
  - Put the chosen TLP into the report dict before rendering.
  - Compute the SHA-256 and audit, then return a `Response` with the bytes and headers.
- **Charts:** `Response(svg, media_type="image/svg+xml", headers={CSP sandbox, nosniff, no-store})`. Any name other than `lifecycle` or `timeline` returns 404.

- [ ] **Step 1: Failing tests** in the scenario style of `tests/test_attachments_api.py`. These checks are mandatory; write a concrete assert for each:
  - (a) GET with no row returns the defaults, a computed `min_tlp`, and `ai_available` false (monkeypatch `describe`).
  - (b) PUT saves and returns the fields. The audit row contains field names only, never the text.
  - (c) PUT with a TLP below the floor gets 422, and PUT with milestones out of order gets 422.
  - (d) A viewer gets 403 on PUT, draft and export.
  - (e) Cross-tenant access gets 404 on all routes.
  - (f) Draft gets 409 when AI is unavailable. With `app.ai.llm.complete` mocked to return JSON, it returns the 3 fields and nothing is saved. With `complete` raising `AIError`, it returns 503 with the generic message.
  - (g) Charts return 200 `image/svg+xml` with the 3 headers and parse as XML. An unknown name gets 404.
  - (h) PDF export:
    - returns 200 `application/pdf`, and the body starts with `%PDF`;
    - `X-Content-SHA256` equals the hash of the body, and the audit `sha256` equals it too;
    - the filename matches `^case-\d{4}-report-TLP-RED-\d{8}\.pdf$`, because the floor is red.
  - (i) JSON export: the `schema` field is right; IOC values are raw (not defanged); `audit` is present only with `full=true`; the TLP query below the floor gets 422.
  - (j) A timeout (`render_pdf_async` patched to raise `RenderTimeout`) gets 504.
  - (k) Logging: one line per operation, and the narrative text never appears in the logs.
- [ ] **Step 2: Implement and run** (PASS), then the full suite and the leak count. Restart the containers.
- [ ] **Step 3: Commit:** `feat(reports): report editing, AI draft, charts and PDF/JSON export API`

---

### Task 7: Frontend Report tab

**Files:**
- Create: `frontend/src/features/cases/CaseReport.tsx`
- Modify: `frontend/src/features/cases/CaseDetail.tsx` (add a `'report'` tab after `'evidence'`), `frontend/src/types/index.ts`

Behaviour is as in the spec's UI section. Read `backend/app/api/v1/reports.py` and `schemas/report.py` for exact shapes. Implementation notes:
- Use `useCanRun` for analyst-and-above checks.
- Use the shared `Modal` and `PageContainer` conventions from `components/layout`.
- Load the chart previews with `api.get(url, {responseType: 'blob'})` and show them through `<img src={URL.createObjectURL(blob)}>`. Revoke the URL on unmount or refresh. Never inject SVG with `dangerouslySetInnerHTML`.
- The `datetime-local` inputs work in local time. Convert to and from UTC ISO strings, and show the computed value as the placeholder.
- Track unsaved changes:
  - a `beforeunload` listener while the form is dirty;
  - an inline confirm when switching CaseDetail tabs while dirty. Expose an `onDirtyChange` prop and have CaseDetail guard `setActiveTab`.
- Export downloads as a blob, taking the filename from `Content-Disposition`. Only one export may run at a time. Show the `detail` from 422 and 504 responses, parsing blob error bodies as JSON.
- The TLP select uses the TLP colours from the spec. Options below `min_tlp` are disabled with a title tooltip.
- The draft confirm uses an inline confirm, not `window.confirm`.

- [ ] **Step 1:** Implement.
- [ ] **Step 2: Verify.**
  - `npm run build` passes.
  - eslint shows no new errors.
  - `docker compose up -d --no-deps --build frontend`.
- [ ] **Step 3: Commit:** `feat(reports): case Report tab with AI draft, chart previews and export`

---

### Task 8: Docs and in-container end-to-end check

- [ ] **Step 1: Docs.**
  - Add an "Incident reports" section to `docs/configuration.md` covering:
    - the OS packages WeasyPrint needs;
    - `REPORT_RENDER_TIMEOUT_SECONDS`;
    - TLP floor rules;
    - that renders have no network access and fonts are embedded;
    - the export audit trail and its SHA-256, and how to verify a PDF with `sha256sum`;
    - that AI drafts are never saved without review.
  - Add a short entry to `docs/features.md`.
- [ ] **Step 2: End-to-end check.**
  - In the container, seed a temporary case with a few events, IOCs and tasks.
  - Call `build_report` and `render_pdf_async` for real, write the PDF to a temp dir, and print its page count (pypdf if available, otherwise the count of `/Type /Page`) and size.
  - Export JSON through the API in-process.
  - Clean up by exact ids and check the leak count is 0.
  - Record the output in the report.
- [ ] **Step 3: Commit:** `docs(reports): incident report configuration and feature notes`
