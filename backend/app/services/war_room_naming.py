"""War room channel naming: a per-tenant template with {id}, {title}, {date} placeholders."""
import re
from datetime import datetime
from typing import Optional

DEFAULT_TEMPLATE = "inc-{id}-{title}"
MAX_LEN = 80  # Slack's channel name limit
PLACEHOLDERS = ("id", "title", "date")
_PH = re.compile(r"\{([^{}]*)\}")


def _sanitise(s: str) -> str:
    s = re.sub(r"[^a-z0-9_-]", "-", s.lower())
    return re.sub(r"-{2,}", "-", s).strip("-")


def validate_template(t: str) -> Optional[str]:
    """An error message, or None when the template is usable."""
    t = (t or "").strip()
    unknown = [n for n in _PH.findall(t) if n not in PLACEHOLDERS]
    if unknown:
        return f"Unknown placeholder {{{unknown[0]}}}; use {{id}}, {{title}} or {{date}}"
    if "{" in _PH.sub("", t) or "}" in _PH.sub("", t):
        return "Unbalanced braces in the channel name template"
    if "{id}" not in t:
        return "The channel name template must include {id} so each case gets a unique channel"
    if len(render_channel_name(t, case_id=9999, title="", created=datetime(2000, 1, 1))) > MAX_LEN - 10:
        return "The channel name template is too long"
    return None


def render_channel_name(template: Optional[str], *, case_id: int, title: str, created: datetime) -> str:
    tpl = (template or "").strip() or DEFAULT_TEMPLATE
    fixed = {"id": f"{case_id:04d}", "date": created.strftime("%Y%m%d")}
    slug = _sanitise(title or "")

    def build(s: str) -> str:
        return _sanitise(_PH.sub(lambda m: s if m.group(1) == "title" else fixed.get(m.group(1), ""), tpl))

    name = build(slug)
    while len(name) > MAX_LEN and slug:  # shorten the title first
        slug = slug[: max(0, len(slug) - (len(name) - MAX_LEN))].strip("-")
        name = build(slug)
    return name[:MAX_LEN].strip("-")


def collision_name(name: str, n: int) -> str:
    """The n-th retry when a channel name is taken: keeps the result within the 80 char limit."""
    suffix = f"-{n}"
    return name[: MAX_LEN - max(3, len(suffix))].rstrip("-") + suffix
