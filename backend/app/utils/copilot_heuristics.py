"""Deterministic helpers for the copilot: indicator detection in free text and
explicit note-text extraction. Pure functions — unit-testable without a DB/LLM."""
import re
from typing import List, Dict, Optional

# ---- indicator detection -------------------------------------------------

_IPV4 = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})\b")
_HASH = re.compile(r"\b([a-fA-F0-9]{64}|[a-fA-F0-9]{40}|[a-fA-F0-9]{32})\b")
_EMAIL = re.compile(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b")
_URL = re.compile(r"\b(https?://[^\s<>\"')\]]+)", re.IGNORECASE)
# Conservative domain match: 2+ labels ending in a plausible TLD.
_DOMAIN = re.compile(
    r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|io|co|ru|cn|info|biz|xyz|top|online|site|support|link|club|live|app|dev|cloud|me|to|cc|ws|tk|pw|su))\b",
    re.IGNORECASE,
)


def _valid_ip(ip: str) -> bool:
    try:
        return all(0 <= int(p) <= 255 for p in ip.split("."))
    except ValueError:
        return False


def detect_indicators(text: str, limit: int = 5) -> List[Dict[str, str]]:
    """Find IOC-like values in free text. Returns [{value, artifact_type}],
    deduped, most-specific types first (url > email > hash > ip > domain)."""
    if not text:
        return []
    found: List[Dict[str, str]] = []
    seen = set()
    consumed_spans: List[tuple] = []

    def take(value: str, atype: str, span: tuple) -> None:
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        consumed_spans.append(span)
        found.append({"value": value, "artifact_type": atype})

    for m in _URL.finditer(text):
        take(m.group(1).rstrip(".,;:"), "url", m.span())
    for m in _EMAIL.finditer(text):
        take(m.group(1), "email", m.span())
    for m in _HASH.finditer(text):
        take(m.group(1).lower(), "file_hash", m.span())
    for m in _IPV4.finditer(text):
        if _valid_ip(m.group(1)):
            take(m.group(1), "ip", m.span())
    for m in _DOMAIN.finditer(text):
        # skip domains inside an already-captured URL/email span
        s, e = m.span()
        if any(s >= cs and e <= ce for cs, ce in consumed_spans):
            continue
        take(m.group(1).lower(), "domain", (s, e))

    return found[:limit]


# ---- explicit note-text extraction ---------------------------------------

_NOTE_WORDS = r"(?:activity\s+log|log\s+entry|timeline\s+entry|timeline|comment|note|annotation|log)"

# "add a comment: X" / "activity log - X"
_NOTE_COLON = re.compile(
    rf"{_NOTE_WORDS}\s*[:\-–—]\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# "add a note that X" / "comment saying X"
_NOTE_THAT = re.compile(
    rf"{_NOTE_WORDS}\s+(?:that|saying|about how)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# "add an activity log <the content...>" — content trails the note word directly
_NOTE_INLINE = re.compile(
    rf"\b(?:add|record|write|create|put|log)\b[^:\n]{{0,20}}?{_NOTE_WORDS}\s*[,;]?\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# leading filler after an inline match ("to the case", "in the timeline", …)
_INLINE_FILLER = re.compile(
    r"^(?:to|in|into|on|onto|for|of)\s+(?:the\s+|this\s+)?(?:case|ticket|incident|timeline)\b[\s:,;.\-]*",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[\"“'](.{8,}?)[\"”']")


def extract_note_text(message: str) -> Optional[str]:
    """Pull the literal note content out of a 'add a comment/note/activity log…'
    request. Returns None when no explicit content can be isolated (e.g. the
    purely referential 'add the activity log')."""
    if not message:
        return None
    for rx in (_NOTE_COLON, _NOTE_THAT):
        m = rx.search(message)
        if m:
            text = m.group(1).strip().strip("\"'“”")
            if len(text) >= 4:
                return text
    m = _NOTE_INLINE.search(message)
    if m:
        text = _INLINE_FILLER.sub("", m.group(1).strip()).strip().strip("\"'“”")
        if len(text) >= 10:
            return text
    m = _QUOTED.search(message)
    if m:
        return m.group(1).strip()
    return None


_NOTE_REQUEST = re.compile(
    rf"\b(?:add|record|log|create|put|write)\b.{{0,40}}\b{_NOTE_WORDS}\b",
    re.IGNORECASE | re.DOTALL,
)


def is_note_request(message: str) -> bool:
    """Does the message ask to record a comment/note/activity-log entry?"""
    return bool(message and _NOTE_REQUEST.search(message))


# Meta/placeholder strings the model produces instead of real note content,
# e.g. "Added activity log", "Note added", "Adding a comment".
_META_NOTE = re.compile(
    rf"^\s*(?:add(?:ed|ing)?|record(?:ed|ing)?|creat(?:ed|ing)|logg?(?:ed|ing)?|writ(?:ten|ing))\b"
    rf"[\s\w]{{0,30}}\b{_NOTE_WORDS}\s*\.?\s*$"
    rf"|^\s*{_NOTE_WORDS}\s+(?:added|recorded|created|logged)\s*\.?\s*$",
    re.IGNORECASE,
)


def is_meta_note(text: str) -> bool:
    """True when `text` describes the act of adding a note rather than being
    note content (the 'Added activity log' failure mode)."""
    return bool(text and len(text) < 80 and _META_NOTE.search(text))


_INTERROGATIVE = re.compile(
    r"^\s*(?:what|how|why|can|could|should|would|is|are|do|does|did|who|when|where|which|tell|explain|show|list|find|search|help)\b",
    re.IGNORECASE,
)


def is_declarative_finding(message: str) -> bool:
    """Heuristic: a statement of investigation findings (not a question/command)
    that might be worth recording on the case timeline."""
    if not message:
        return False
    msg = message.strip()
    if len(msg) < 30 or msg.endswith("?"):
        return False
    if _INTERROGATIVE.match(msg):
        return False
    return len(msg.split()) >= 6
