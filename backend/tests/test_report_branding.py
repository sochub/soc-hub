import base64
import json

from app.api.v1.reports import _json_bytes
from app.reports.pdf import render_html
from tests.audit_fixtures import hdr, run
from tests.test_report_render import make_report

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
URL = "/api/v1/tenants/current/report-branding"


def test_branding_api_validates_and_is_admin_only():
    async def body(c, w):
        admin, analyst = hdr(w, "admin"), hdr(w, "analyst")
        r = await c.put(URL, json={"org_name": "Acme SOC", "accent": "#0f766e", "disclaimer": "Internal only"}, headers=admin)
        assert r.status_code == 200 and r.json() == {"org_name": "Acme SOC", "accent": "#0f766e",
                                                     "disclaimer": "Internal only", "has_logo": False}
        assert (await c.put(URL, json={"accent": "red;}body{x"}, headers=admin)).status_code == 422
        assert (await c.put(URL, json={"org_name": "x"}, headers=analyst)).status_code == 403
        assert (await c.get(URL, headers=analyst)).json()["org_name"] == "Acme SOC"

        up = lambda data, h=admin: c.put(f"{URL}/logo", files={"file": ("l.png", data, "image/png")}, headers=h)
        assert (await up(b"<svg onload=alert(1)>")).status_code == 415
        assert (await up(PNG + b"\x00" * (256 * 1024))).status_code == 413
        assert (await up(PNG, analyst)).status_code == 403
        assert (await up(PNG)).json()["has_logo"] is True
        logo = await c.get(f"{URL}/logo", headers=analyst)
        assert logo.status_code == 200 and logo.headers["content-type"] == "image/png" and logo.content == PNG
        assert (await c.delete(f"{URL}/logo", headers=admin)).json()["has_logo"] is False
        assert (await c.get(f"{URL}/logo", headers=analyst)).status_code == 404
    run(body)


def test_render_uses_branding_and_never_trusts_accent_or_logo():
    logo = "data:image/png;base64," + base64.b64encode(PNG).decode()
    html = render_html(make_report(branding={"org_name": "Acme <SOC>", "accent": "#0f766e",
                                             "disclaimer": "Internal only", "logo": logo}))
    assert "Acme &lt;SOC&gt;" in html and "#0f766e" in html and logo in html and "Internal only" in html

    html = render_html(make_report(branding={"accent": "red;}body{display:none", "logo": "javascript:alert(1)"}))
    assert "display:none" not in html and "javascript:" not in html and "#4f46e5" in html


def test_render_without_branding_keeps_working():
    html = render_html(make_report())  # reports built before branding existed
    assert "SOC Hub" in html and "Appendix C. Rating scales" in html


def test_json_export_drops_logo():
    out = json.loads(_json_bytes(make_report(branding={"org_name": "Acme", "logo": "data:image/png;base64,AAAA"})))
    assert out["branding"] == {"org_name": "Acme"}
