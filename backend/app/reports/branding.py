"""Per-tenant report branding: validation and the values the PDF template consumes."""
import base64
import re
from typing import Optional

ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_ACCENT = "#4f46e5"
LOGO_MAX_BYTES = 256 * 1024
DEFAULT_DISCLAIMER = (
    "This report contains information about a security incident and is intended only for the "
    "recipients named in its distribution. Handle it according to its TLP marking. Findings reflect "
    "the evidence available when the report was generated and may change as the investigation continues."
)


def sniff_logo(data: bytes) -> Optional[str]:
    """MIME type for a PNG or JPEG by magic bytes; None for anything else (SVG is refused: it can carry script)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def accent(value: Optional[str]) -> str:
    # The accent is interpolated into CSS: only ever emit a validated #rrggbb.
    return value if value and ACCENT_RE.match(value) else DEFAULT_ACCENT


def for_report(tenant) -> dict:
    """Branding block stored in the report dict (JSON-safe; the logo is added only for rendering)."""
    return {"org_name": tenant.report_org_name or tenant.name, "accent": accent(tenant.report_accent),
            "disclaimer": tenant.report_disclaimer or DEFAULT_DISCLAIMER, "has_logo": bool(tenant.report_logo)}


def logo_data_uri(tenant) -> Optional[str]:
    mime = sniff_logo(tenant.report_logo or b"")
    return f"data:{mime};base64,{base64.b64encode(tenant.report_logo).decode()}" if mime else None
