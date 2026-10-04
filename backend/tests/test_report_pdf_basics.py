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
    assert calls == ["http://169.254.169.254/x"]  # the fetch was attempted and denied
