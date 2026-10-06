"""PDF rendering: Jinja template -> WeasyPrint, no network, embedded fonts."""
import asyncio
import base64
import logging
import re
import weakref
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.request import urlopen

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape
from weasyprint import HTML, URLFetcher
from weasyprint.urls import URLFetcherResponse

from app.core.config import settings
from app.reports import tlp as tlp_mod
from app.reports.builder import PDF_LIMITS, compute_truncated
from app.reports.charts import lifecycle_svg, timeline_svg
from app.reports.defang import defang, scrub_userinfo
from app.reports.milestones import aware, fmt_duration

# R3: WeasyPrint logs blocked URLs (attacker-influenced case content) at ERROR; never let them reach logs.
logging.getLogger("weasyprint").setLevel(logging.CRITICAL)
logging.getLogger("fontTools").setLevel(logging.CRITICAL)

FONTS = Path(__file__).parent / "fonts"
TEMPLATES = Path(__file__).parent / "templates"
_FACES = [("IBM Plex Sans", "IBMPlexSans-Regular.ttf", 400), ("IBM Plex Sans", "IBMPlexSans-SemiBold.ttf", 600),
          ("Roboto Mono", "RobotoMono-Regular.ttf", 400)]


def deny_all_fetcher(url, timeout=10, ssl_context=None):
    """Only inline data: URLs. Case content must never make the server fetch anything.

    Returns a dict (string/mime_type) for data: URLs; raises ValueError otherwise.
    """
    if isinstance(url, str) and url.strip().lower().startswith("data:"):
        with urlopen(url.strip()) as resp:  # nosec B310  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- guarded: data: only
            return {"string": resp.read(), "mime_type": resp.headers.get_content_type(), "url": url}
    raise ValueError("blocked url")


class _DenyFetcher(URLFetcher):
    """WeasyPrint >= 68 requires a URLFetcher; route every fetch through deny_all_fetcher
    (looked up at call time so it can be monkeypatched)."""

    def fetch(self, url, headers=None):
        r = deny_all_fetcher(url)
        return URLFetcherResponse(r["url"], r["string"], {"Content-Type": r["mime_type"]})


@lru_cache(maxsize=1)
def font_face_css() -> str:
    out = []
    for family, file, weight in _FACES:
        b64 = base64.b64encode((FONTS / file).read_bytes()).decode()
        out.append(f"@font-face{{font-family:'{family}';font-weight:{weight};"
                   f"src:url(data:font/ttf;base64,{b64}) format('truetype');}}")
    return "\n".join(out)


def html_to_pdf(html: str) -> bytes:
    return HTML(string=html, url_fetcher=_DenyFetcher(), base_url=None).write_pdf()


# --- template filters ---

_SCHEME = re.compile(r"[A-Za-z][\w+.-]*:[/\\]+")
_LEADING_SEP = re.compile(r"[/\\]+")
_AUTH_END = re.compile(r"[/\\?#]")


def strip_userinfo(v: str) -> str:
    """Drop everything before the last '@' in the URL authority (any scheme, or none).

    The authority ends at the first '/', '\\', '?' or '#', so an '@' in the query or fragment
    (e.g. ?email=victim@corp.com) never replaces the real host. Known limitation (R8): a password
    containing a literal '#' or '?' is only partly stripped; correct hosts matter more, and real
    credentials in IOC URLs almost always use the plain user:pass@ form."""
    v = v.strip()
    sm = _SCHEME.match(v) or _LEADING_SEP.match(v)
    head, rest = (sm.group(0), v[sm.end():]) if sm else ("", v)
    end = _AUTH_END.search(rest)
    end = end.start() if end else len(rest)
    auth = rest[:end]
    if "@" not in auth:
        return v
    return head + auth.rsplit("@", 1)[1] + rest[end:]


_strip_userinfo = strip_userinfo  # backwards-compatible alias


def _defang_filter(value, itype) -> str:
    v = "" if value is None else str(value)
    if (itype or "").lower() == "url":
        v = strip_userinfo(v)
    return defang(v, itype)


_TEXT_URL = re.compile(r"(?:https?|ftp)://\S+", re.I)


def _defang_text_filter(value) -> Markup:
    """Free text with every http(s)/ftp URL defanged; everything else escaped as usual."""
    s = "" if value is None else str(value)
    out, pos = [], 0
    for m in _TEXT_URL.finditer(s):
        out += [escape(s[pos:m.start()]), escape(defang(scrub_userinfo(m.group(0)), "url"))]
        pos = m.end()
    out.append(escape(s[pos:]))
    return Markup("".join(out))  # nosec B704  # nosemgrep: python.flask.security.xss.audit.explicit-unescape-with-markup.explicit-unescape-with-markup -- every piece escaped above


def _dt(value, suffix=True) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    out = aware(value).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return f"{out} UTC" if suffix else out


def _filesize(n) -> str:
    if n is None:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


_ENV = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True, undefined=StrictUndefined)  # nosemgrep: python.flask.security.xss.audit.direct-use-of-jinja2.direct-use-of-jinja2 -- autoescape=True
_ENV.filters.update(defang=_defang_filter, defang_text=_defang_text_filter, dt=_dt, duration=fmt_duration, filesize=_filesize)


def render_html(report: dict) -> str:
    tlp = report["report"]["tlp"]
    tlp = tlp if tlp in tlp_mod.LABEL else "red"  # unknown marking: fail closed to the strictest
    bg, fg = tlp_mod.COLORS[tlp]
    return _ENV.get_template("report.html.j2").render(
        report=report,
        # Trusted constant CSS (our vendored fonts). <style> is raw text: entity-escaping would break the
        # @font-face rules and silently fall back to DejaVu.
        font_css=Markup(font_face_css()), limits=PDF_LIMITS, truncated=compute_truncated(report),  # nosec B704  # nosemgrep: python.flask.security.xss.audit.explicit-unescape-with-markup.explicit-unescape-with-markup -- constant vendored CSS
        tlp_label=tlp_mod.LABEL[tlp], tlp_bg=bg, tlp_fg=fg, tlp_border="#000000" if tlp == "white" else bg,
        case_ref=f"Case #{int(report['case']['id']):04d}",
        lifecycle_svg=lifecycle_svg(report["milestones"]), timeline_svg=timeline_svg(report["timeline"]))


def render_pdf(report: dict) -> bytes:
    return html_to_pdf(render_html(report))


class RenderTimeout(Exception):
    pass


# One render at a time per event loop (the API process runs a single loop, so: one per process).
# asyncio.Semaphore binds to the first loop that contends on it, so a module-level instance would break
# callers that run several loops over time (asyncio.run per task, tests); keep one per running loop.
_SEMS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _SEMS.get(loop)
    if sem is None:
        sem = _SEMS[loop] = asyncio.Semaphore(1)
    return sem


async def render_pdf_async(report: dict) -> bytes:
    """Render off the event loop, one render per process.

    The permit is held until the render THREAD finishes, not until the caller stops waiting:
    threads cannot be cancelled, so on timeout the caller gets RenderTimeout while the thread runs to
    completion and only then frees the slot. Waiting for the slot and waiting for the render each
    use the configured timeout (read at call time), so a caller waits at most ~2x the timeout.
    """
    timeout = settings.REPORT_RENDER_TIMEOUT_SECONDS
    sem = _sem()
    try:
        await asyncio.wait_for(sem.acquire(), timeout)
    except asyncio.TimeoutError:
        raise RenderTimeout() from None

    def _release(f: asyncio.Future) -> None:
        sem.release()
        if not f.cancelled():
            f.exception()  # mark retrieved: no "exception was never retrieved" noise

    try:
        fut = asyncio.ensure_future(asyncio.to_thread(render_pdf, report))
    except BaseException:
        sem.release()
        raise
    fut.add_done_callback(_release)
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout)
    except asyncio.TimeoutError:
        raise RenderTimeout() from None
