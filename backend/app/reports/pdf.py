"""PDF rendering primitives: no network, embedded fonts."""
import base64
from functools import lru_cache
from pathlib import Path
from urllib.request import urlopen

from weasyprint import HTML, URLFetcher
from weasyprint.urls import URLFetcherResponse

FONTS = Path(__file__).parent / "fonts"
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
