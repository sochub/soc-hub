"""PDF rendering: Jinja template -> WeasyPrint, no network, embedded fonts."""
import asyncio
import base64
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.request import urlopen

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
from weasyprint import HTML, URLFetcher
from weasyprint.urls import URLFetcherResponse

from app.core.config import settings
from app.reports import tlp as tlp_mod
from app.reports.builder import PDF_LIMITS, compute_truncated
from app.reports.charts import lifecycle_svg, timeline_svg
from app.reports.defang import defang
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
        with urlopen(url.strip()) as resp:  # data: scheme only, never touches the network
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

_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_AUTHORITY = re.compile(r"[^/?#]*")


def _strip_userinfo(v: str) -> str:
    """Drop everything before the last '@' in the URL authority (any scheme, or none)."""
    sm = _SCHEME.match(v)
    if sm:
        head, rest = sm.group(0), v[sm.end():]
    elif v.startswith("//"):
        head, rest = "//", v[2:]
    else:
        head, rest = "", v
    auth = _AUTHORITY.match(rest).group(0)
    if "@" not in auth:
        return v
    return head + auth.rsplit("@", 1)[1] + rest[len(auth):]


def _defang_filter(value, itype) -> str:
    v = "" if value is None else str(value)
    if (itype or "").lower() == "url":
        v = _strip_userinfo(v)
    return defang(v, itype)


def _dt(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return aware(value).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _filesize(n) -> str:
    if n is None:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


_ENV = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True, undefined=StrictUndefined)
_ENV.filters.update(defang=_defang_filter, dt=_dt, duration=fmt_duration, filesize=_filesize)


def render_html(report: dict) -> str:
    tlp = report["report"]["tlp"]
    tlp = tlp if tlp in tlp_mod.LABEL else "red"  # unknown marking: fail closed to the strictest
    bg, fg = tlp_mod.COLORS[tlp]
    return _ENV.get_template("report.html.j2").render(
        report=report,
        # Trusted constant CSS (our vendored fonts). <style> is raw text: entity-escaping would break the
        # @font-face rules and silently fall back to DejaVu.
        font_css=Markup(font_face_css()), limits=PDF_LIMITS, truncated=compute_truncated(report),
        tlp_label=tlp_mod.LABEL[tlp], tlp_bg=bg, tlp_fg=fg, tlp_border="#000000" if tlp == "white" else bg,
        case_ref=f"Case #{int(report['case']['id']):04d}",
        lifecycle_svg=lifecycle_svg(report["milestones"]), timeline_svg=timeline_svg(report["timeline"]))


def render_pdf(report: dict) -> bytes:
    return html_to_pdf(render_html(report))


class RenderTimeout(Exception):
    pass


_SEM = asyncio.Semaphore(1)  # one render per process


async def render_pdf_async(report: dict) -> bytes:
    """Render off the event loop. On timeout the worker thread keeps running to completion
    (threads cannot be cancelled) — documented limitation; the semaphore is released regardless."""
    timeout = settings.REPORT_RENDER_TIMEOUT_SECONDS
    async with _SEM:
        try:
            return await asyncio.wait_for(asyncio.to_thread(render_pdf, report), timeout)
        except asyncio.TimeoutError:
            raise RenderTimeout()
