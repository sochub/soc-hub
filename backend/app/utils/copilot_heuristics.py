"""Deterministic helpers for the copilot: indicator detection in free text and
explicit note-text extraction. Pure functions — unit-testable without a DB/LLM."""
import ipaddress
import re
from typing import Dict, Iterable, List, Optional

# ---- indicator detection -------------------------------------------------

_IPV4 = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})\b")
_HASH = re.compile(r"\b([a-fA-F0-9]{64}|[a-fA-F0-9]{40}|[a-fA-F0-9]{32})\b")
# Every quantifier is bounded and labels can't contain the "." that separates them, so each start position costs
# a bounded amount of work: matching stays linear in the text (unbounded `+` here was quadratic, i.e. ReDoS).
_EMAIL = re.compile(r"\b([a-zA-Z0-9._%+-]{1,64}@(?:[a-zA-Z0-9-]{1,63}\.){1,8}[a-zA-Z]{2,24})\b")
_URL = re.compile(r"\b(https?://[^\s<>\"')\]]+)", re.IGNORECASE)
# ponytail: a hand-kept list, not the IANA root zone. A generic "any 2-24 letters" TLD reads `down.Then` and
# `main.rs` as domains, so unknown TLDs are missed on purpose; TLDs that are everyday words or common file
# extensions (it, in, is, at, be, no, us, today, one, work, md, sh, rs…) are left out for the same reason.
# Ceiling: the public suffix list.
TLDS = frozenset("""com net org info biz gov edu mil int io co ru cn xyz top online site support link club live app dev
cloud me to cc ws tk pw su de uk fr nl es pl eu ch se dk fi cz ro hu gr pt ie ca mx br ar cl au nz jp kr vn th tr ir il
ua kz za ng hk sg tw ph pk ai gg ly tv fm gd vc ms ac li lu ee lv lt sk si hr bg ml ga cf gq shop zip mov store tech
space website icu vip pro mobi bid loan download stream click monster cyou buzz cfd sbs quest lol cam review trade
science ltd blog media studio design email digital network services solutions agency""".split())
# Conservative domain match: 2+ labels ending in a known TLD that is not followed by a further label
# (`setup.com.exe` is a file, `evil.co.uk` must not stop at `evil.co`).
_DOMAIN = re.compile(
    r"\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,8}(?:" + "|".join(sorted(TLDS)) + r"))\b(?!\.[a-z0-9-])",
    re.IGNORECASE,
)
# ponytail: the two-level public suffixes seen most; alone they are a registry, not a domain.
# Ceiling: the public suffix list.
_TWO_LEVEL_SUFFIXES = ("co.uk", "com.au", "co.jp", "com.br", "co.nz", "co.za", "com.mx", "com.ar", "co.in", "org.uk",
                       "gov.uk", "ac.uk", "com.tr", "com.cn")
# TLDs that are also file extensions (`run.me`, `setup.app`): a file cue just before the token wins.
_FILE_TLDS = {"zip", "mov", "app", "dev", "me", "pl", "cc", "ml", "download"}
_FILE_CUE = re.compile(r"\b(?:dropped|file|ran|executed|opened|attachment)(?:\s+\S+){0,2}\s+[\"'`]?$", re.IGNORECASE)
# `invoice.pdf.zip`: a document/binary extension right before the TLD means a file name
_FILE_EXTS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "exe", "dll", "bat", "ps1", "vbs", "jar",
              "msi", "scr", "lnk", "iso", "rar", "tar", "gz", "json", "xml", "yaml", "yml", "html", "htm", "png", "jpg",
              "jpeg", "gif"}
# "agent version 7.14.2.1", "v1.2.3.4", "Chrome 120.0.60.71": a dotted quad after a version cue is not an IP
_VERSION_CUE = re.compile(r"\b(?:version|ver\.?|v|build|release|chrome|chromium|firefox|safari)[\s:=]*$", re.IGNORECASE)
# a dash-less UUID is 32 hex chars too: "uuid 5f0c…" / "id: 5f0c…" is an identifier, not an MD5
_UUID_CUE = re.compile(r"\b(?:uuid|guid|id)\s*[:=#]?\s*$", re.IGNORECASE)
_CUE_WINDOW = 40  # how far back a cue is looked for (keeps the cue regexes off the whole text)


def _valid_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:  # out-of-range octet, leading zeros
        return False
    # never an indicator; private (RFC1918) ranges stay, they matter in incidents
    return not (addr.is_loopback or addr.is_unspecified or addr.is_link_local)


def _is_file_name(domain: str, before: str) -> bool:
    labels = domain.lower().split(".")
    return labels[-2] in _FILE_EXTS or (labels[-1] in _FILE_TLDS and bool(_FILE_CUE.search(before)))


# Analysts defang indicators so nobody clicks them: hxxp://evil[.]com, 185[.]220[.]101[.]5, bob[at]evil[.]com
_REFANG = ((re.compile(r"\[\.\]|\(\.\)|\{\.\}|\[dot\]", re.IGNORECASE), "."), (re.compile(r"\[:\]"), ":"),
           (re.compile(r"\[@\]|\[at\]", re.IGNORECASE), "@"), (re.compile(r"hxxp(s?)://", re.IGNORECASE), r"http\1://"))


def refang(text: str) -> str:
    """Undo defanging so detect_indicators sees (and callers store) the real value."""
    for rx, to in _REFANG:
        text = rx.sub(to, text)
    return text


MAX_SCAN_CHARS = 64_000


def detect_indicators(text: str, limit: int = 5) -> List[Dict[str, str]]:
    """Find IOC-like values in free text. Returns [{value, artifact_type}],
    deduped, most-specific types first (url > email > hash > ip > domain).
    Only the first MAX_SCAN_CHARS characters are scanned."""
    if not text:
        return []
    text = text[:MAX_SCAN_CHARS]
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

    def before(start: int) -> str:
        return text[max(0, start - _CUE_WINDOW):start]

    for m in _URL.finditer(text):
        take(m.group(1).rstrip(".,;:"), "url", m.span())
    for m in _EMAIL.finditer(text):
        take(m.group(1), "email", m.span())
    for m in _HASH.finditer(text):
        if len(m.group(1)) == 32 and _UUID_CUE.search(before(m.start())):
            continue
        take(m.group(1).lower(), "file_hash", m.span())
    for m in _IPV4.finditer(text):
        if _valid_ip(m.group(1)) and not _VERSION_CUE.search(before(m.start())):
            take(m.group(1), "ip", m.span())
    for m in _DOMAIN.finditer(text):
        # skip domains inside an already-captured URL/email span
        s, e = m.span()
        if any(s >= cs and e <= ce for cs, ce in consumed_spans):
            continue
        value = m.group(1).lower()
        if value in _TWO_LEVEL_SUFFIXES or _is_file_name(value, before(s)):
            continue
        take(value, "domain", (s, e))

    return found[:limit]


# ---- explicit note-text extraction ---------------------------------------

# Word-bounded on both sides: "syslog-ng" and "catalog - x" contain "log" but are not note words.
_NOTE_WORDS = r"\b(?:activity\s+log|log\s+entry|timeline\s+entry|timeline|comment|note|annotation|log)\b"
_NOTE_VERBS = r"\b(?:add|record|write|create|put|log|leave|post)\b"
# The request itself: verb, an optional pronoun object and preposition ("add this to the …"), determiners, then
# the note word. Deliberately tight — "log in to the firewall and check the timeline" must not match.
_NOTE_HEAD = (
    rf"{_NOTE_VERBS}\s+(?:(?:this|that|it|these|those|them|the\s+following)\s+)?(?:(?:to|in|into|on|as)\s+)?"
    rf"(?:(?:a|an|the|this|that|my|new|another|quick|short|brief)\s+){{0,3}}(?:case\s+)?{_NOTE_WORDS}"
)

# "add a comment: X" / "activity log - X". A dash needs a space on one side, so "log-in" is not a separator.
_NOTE_COLON = re.compile(
    rf"{_NOTE_WORDS}(?:\s*:\s*|\s+[\-–—]\s*|\s*[\-–—]\s+)(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# "add a note that X" / "comment saying X" / "record that X"
_NOTE_THAT = re.compile(
    rf"(?:{_NOTE_WORDS}|\brecord)\s+(?:that|saying|about how)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
# "add an activity log <the content...>" — content trails the note word directly
_NOTE_INLINE = re.compile(rf"{_NOTE_HEAD}\s*[,;]?\s+(.+)$", re.IGNORECASE | re.DOTALL)
# leading filler after an inline match ("to the case", "in the timeline", …)
_INLINE_FILLER = re.compile(
    r"^(?:to|in|into|on|onto|for|of)\s+(?:the\s+|this\s+)?(?:case|ticket|incident|timeline)\b[\s:,;.\-]*",
    re.IGNORECASE,
)
# What follows the note word describes the note instead of being it ("a timeline of the attack", "a comment
# summarizing the analysis"): the model composes that text; the instruction itself is never saved as the note.
_COMPOSE_LEAD = re.compile(
    r"^(?:of|for|about|on|regarding|covering|with|summari[sz]ing|describing|explaining|detailing|listing)\b",
    re.IGNORECASE,
)
# Real double quotes only: an apostrophe ("the user's mailbox … the sender's domain") is not a quote.
_QUOTE_CHARS = "\"“”"
_QUOTED = re.compile(r"[\"“](.{8,}?)[\"”]")


def extract_note_text(message: str) -> Optional[str]:
    """Pull the literal note content out of a 'add a comment/note/activity log…'
    request. Returns None when no explicit content can be isolated (e.g. the
    purely referential 'add the activity log', or a request to compose one)."""
    if not message:
        return None
    # Whichever marker comes first in the message wins ("note that … the catalog - nothing unusual" is one clause).
    marked = [m for m in (_NOTE_THAT.search(message), _NOTE_COLON.search(message)) if m]
    if marked:
        text = min(marked, key=lambda m: m.start()).group(1).strip().strip(_QUOTE_CHARS)
        if len(text) >= 4:
            return text
    m = _NOTE_INLINE.search(message)
    if m:
        text = _INLINE_FILLER.sub("", m.group(1).strip()).strip().strip(_QUOTE_CHARS)
        if _COMPOSE_LEAD.match(text):
            return None
        if len(text) >= 10:
            return text
    m = _QUOTED.search(message)
    if m:
        return m.group(1).strip()
    return None


_NOTE_REQUEST = re.compile(rf"{_NOTE_HEAD}|^\s*(?:please\s+)?(?:record|note|log)\s+that\b", re.IGNORECASE)
# A different action asked for earlier in the same message ("create a case and add a note …"): the model
# resolves the whole request, so the note path must not swallow it.
_OTHER_ACTION = re.compile(r"\b(?:create|open|make)\s+(?:an?\s+)?(?:new\s+)?case\b|\bas\s+an?\s+(?:artifact|ioc)\b",
                           re.IGNORECASE)


def is_note_request(message: str) -> bool:
    """Does the message ask to record a comment/note/activity-log entry?"""
    m = _NOTE_REQUEST.search(message or "")
    return bool(m) and not _OTHER_ACTION.search(message[:m.start()])


# Meta/placeholder strings the model produces instead of real note content,
# e.g. "Added activity log", "Note added", "Adding a comment".
_META_NOTE = re.compile(
    rf"^\s*(?:add(?:ed|ing)?|record(?:ed|ing)?|creat(?:ed|ing)|logg?(?:ed|ing)?|writ(?:ten|ing))\b"
    rf"[\s\w]{{0,30}}{_NOTE_WORDS}\s*\.?\s*$"
    rf"|^\s*{_NOTE_WORDS}\s+(?:added|recorded|created|logged)\s*\.?\s*$",
    re.IGNORECASE,
)


def is_meta_note(text: str) -> bool:
    """True when `text` describes the act of adding a note rather than being
    note content (the 'Added activity log' failure mode)."""
    return bool(text and len(text) < 80 and _META_NOTE.search(text))


# Questions and commands: neither is a finding, so neither gets the "record this in the timeline?" chip.
_NOT_A_FINDING = re.compile(
    r"^\s*(?:(?:please|pls|kindly|can you|could you)\s+)?(?:what|how|why|can|could|should|would|is|are|do|does|did|who"
    r"|when|where|which|tell|explain|show|list|find|search|help|update|change|set|close|open|reopen|create|add|remove"
    r"|delete|mark|assign|escalate|summari[sz]e|give|write|draft|generate|check|look|run|apply|tag|resolve|block"
    r"|isolate|make|get|analy[sz]e|review|correlate|compare|describe|identify|provide|suggest|recommend|let)\b"
    r"|\bplease[\s.!]*$",
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
    if _NOT_A_FINDING.search(msg):
        return False
    return len(msg.split()) >= 6


# ── grounding a model-proposed action in what was actually asked ──────────────────────────────────────────────
# Small models fill every field of an action's shape: asked to "tag this as data exfiltration" they also propose
# a status and a severity (sometimes invented ones). A field survives only if the conversation asked for it.
_STATUS_ASK = re.compile(r"\b(status|clos(e|ed|es|ing)|resolv(e|ed|es|ing)|re-?open(ed|s|ing)?|in[ _-]progress|pending|"
                         r"(set|move|change|mark)\b.{0,30}\b(new|open))\b", re.IGNORECASE)
_SEVERITY_ASK = re.compile(r"\b(severity|sev|priority|critical|escalat(e|ed|es|ing)|downgrad(e|ed|es|ing)|rais(e|ed|es|ing)|"
                           r"lower(ed|s|ing)?|bump(ed|s|ing)?|(to|as|is) (high|medium|low|info))\b", re.IGNORECASE)
_TAG_ASK = re.compile(r"\b(tag(s|ged|ging)?|label(s|ed|led|ing)?)\b", re.IGNORECASE)


_AFFIRMATION = re.compile(r"^\W*(yes|yep|yeah|ok(ay)?|sure|please do|do (it|that|so)|go ahead|confirm(ed)?|"
                          r"(add|apply|run|tag|set) (it|that|them))\b", re.IGNORECASE)
_RELATED_ASK = re.compile(
    r"\b(related|similar|linked|connected)\b.{0,30}\b(cases?|incidents?)\b|"
    r"\b(cases?|incidents?)\b.{0,40}\b(related|similar|shar(e|es|ed|ing)|same (ioc|indicator|ip|domain|hash|host))|"
    r"\bcorrelat(e|es|ed|ing|ion)\b|\bseen (this|it|them) (before|elsewhere)\b", re.IGNORECASE)


def is_affirmation(message: str) -> bool:
    """"yes" / "do it" / "go ahead": the request is whatever the turn before offered."""
    return bool(_AFFIRMATION.match(message or "")) and len((message or "").split()) <= 8


def related_request_value(message: str) -> Optional[str]:
    """For "find related cases"-style messages: "" to correlate the case's own indicators, or the one indicator
    the message names. None when the message is not asking for related cases. Decided here, not by the model:
    a small model answers this with whatever action the previous turn was about."""
    if not _RELATED_ASK.search(message or ""):
        return None
    found = detect_indicators(refang(message), limit=1)
    return found[0]["value"] if found else ""


_LEVEL = r"(critical|high|medium|low|info)(?![-\w])"
_EXPLICIT_SEVERITY = re.compile(
    rf"\b(?:severity|priority)\b[^.?!]{{0,30}}?\b{_LEVEL}|"
    rf"\b(?:rais\w+|lower\w*|escalat\w+|downgrad\w+|bump\w*)\b[^.?!]{{0,30}}?\bto\s+{_LEVEL}", re.IGNORECASE)
_STATE = r"(new|open|in[ _-]progress|pending|resolved|closed)(?![-\w])"
_EXPLICIT_STATUS = re.compile(
    rf"\bstatus\b[^.?!]{{0,20}}?\b(?:to|as|=|:)\s*{_STATE}|\bmark\w*\b[^.?!]{{0,25}}?\bas\s+{_STATE}", re.IGNORECASE)
_REOPEN = re.compile(r"\bre-?open\b", re.IGNORECASE)
_QUESTION = re.compile(r"^\W*(what|why|how|should|could|can|is|are|was|were|does|do|did|who|when|where|which)\b|\?\s*$",
                       re.IGNORECASE)


def explicit_case_update(message: str) -> Optional[dict]:
    """update_case params an instruction states outright ("raise the severity to high", "mark this as resolved",
    "reopen the case"), else None. The net under a model that answered such a request with nothing; questions
    ("should we raise the severity to high?") are not instructions."""
    text = message or ""
    if _QUESTION.search(text):
        return None
    params: dict = {}
    m = _EXPLICIT_SEVERITY.search(text)
    if m:
        params["severity"] = (m.group(1) or m.group(2)).lower()
    m = _EXPLICIT_STATUS.search(text)
    if m:
        params["status"] = re.sub(r"[ -]", "_", (m.group(1) or m.group(2)).lower())
    elif _REOPEN.search(text):
        params["status"] = "open"
    return params or None


def ground_action(action: Optional[dict], asked: str, *, statuses: Iterable[str], severities: Iterable[str],
                  message: Optional[str] = None) -> Optional[dict]:
    """Drop what the model added to an action beyond the request. `asked` is the user's message plus the turn or
    two before it (so "yes, do it" keeps what the assistant just offered); `message` is the user's message alone
    (defaults to `asked`). Returns the action, trimmed, or None
    when nothing asked-for is left. Only update_case and find_related are trimmed; the others have their own
    checks (literal note text, indicators from the conversation)."""
    if not action:
        return action
    params = dict(action.get("params") or {})
    if action.get("type") == "update_case":
        status = str(params.get("status") or "").strip().lower()
        if status not in set(statuses) or not _STATUS_ASK.search(asked):
            params.pop("status", None)
        severity = str(params.get("severity") or "").strip().lower()
        if severity not in set(severities) or not _SEVERITY_ASK.search(asked):
            params.pop("severity", None)
        if not params.get("tags") or not _TAG_ASK.search(asked):
            params.pop("tags", None)
        if not any(k in params for k in ("status", "severity", "tags")):
            return None
    elif action.get("type") == "find_related":
        # An invented search value ("phishing campaign", or words from an earlier turn) can never match: only a
        # value in this message, or an indicator from the recent turns, is used. Without one the search
        # correlates the current case's own indicators.
        value = str(params.get("value") or "").strip().lower()
        recent = {d["value"].lower() for d in detect_indicators(refang(asked), limit=50)}
        if value and value not in refang(message if message is not None else asked).lower() and value not in recent:
            params.pop("value", None)
    return {**action, "params": params}
