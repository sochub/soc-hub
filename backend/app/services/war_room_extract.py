"""Pure text extraction for SOC-bot war-room suggestions: artifacts (indicators,
hostnames, usernames) and incident-milestone times from a Slack message.
No DB, no network."""
import html
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.utils.copilot_heuristics import detect_indicators

# ---- Slack normalisation --------------------------------------------------

_SLACK_REF = re.compile(r"<[@#!][^>]*>")                  # <@U1> <#C1|ir> <!here>
_SLACK_LINK = re.compile(r"<([^|<>\s]+)(?:\|[^<>]*)?>")   # <url|label> / <url>


def normalize_slack_text(text: str) -> str:
    text = _SLACK_REF.sub(" ", text or "")
    text = _SLACK_LINK.sub(lambda m: re.sub(r"^mailto:", "", m.group(1)), text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# Input bounds: Slack allows 40k chars; war-room signal lives in the first few thousand.
MAX_RAW_CHARS = 16000
MAX_TEXT_CHARS = 4000
MAX_TOKEN_CHARS = 512


@dataclass(frozen=True)
class Candidate:
    kind: str          # "artifact" | "milestone"
    payload: dict      # {"type","value"} or {"key","at"}
    value_key: str


def _artifact(type_: str, value: str) -> Candidate:
    return Candidate("artifact", {"type": type_, "value": value}, f"{type_}:{value.lower()}")


# ---- hostnames / usernames ------------------------------------------------
# Precision over recall: every rule below exists to keep ordinary words and file
# names out of the suggestions. Token matching is procedural (split, then test
# each token), so cost stays linear in the (capped) input.

_URL_OR_EMAIL = re.compile(r"https?://\S+|[\w.%+-]+@[\w.-]+\.\w+", re.IGNORECASE)
_TOKEN_SPLIT = re.compile(r"[^\s,;()\[\]{}<>\"'`]+")
_LABEL = re.compile(r"[a-z0-9-]+")
_INTERNAL_SUFFIXES = {"local", "corp", "internal", "lan", "ad"}
# NAME-123 first groups that are products, protocols or ticket ids, not machines
_NOT_HOST_PREFIX = {"cve", "sha", "md", "md5", "iso", "utf", "tls", "ssl", "rfc", "inc", "task", "ticket", "case",
                    "kb", "ir", "soc", "http", "https", "tcp", "udp", "covid", "windows", "win", "python", "office",
                    "aes", "rsa", "des", "sql", "ipv", "ms", "ssh", "ftp", "dns"}
_HOST_CUE = re.compile(r"\b(?:host|hostname|machine|endpoint)[:\s]+([A-Za-z0-9][\w.-]*)", re.IGNORECASE)

# DOMAIN\user; the lookbehind rejects Windows paths (C:\Users\bob) and UNC shares (\\srv\share)
_DOMAIN_USER = re.compile(r"(?<![\\\w:])[A-Za-z][\w-]*\\[A-Za-z0-9][\w.-]*")
_NOT_DOMAIN = {"users", "windows", "program", "programdata", "system32", "temp", "appdata", "documents"}
_USER_CUE = re.compile(r"\b(?:user(?:name)?|account|login)[:\s]+([A-Za-z0-9][\w.-]*)", re.IGNORECASE)
# "reset password for jdoe", "disable the account for jdoe" (up to 2 words between)
_USER_VERB = re.compile(r"\b(?:reset|disabl|lock|suspend)\w*(?:\s+\w+){0,2}?\s+for\s+([A-Za-z0-9][\w.-]*)", re.IGNORECASE)

# Words that follow a cue but are not names ("host is down", "account takeover")
_FILLER = set("""is was are has had the a an and or to of in on at that this these those with from for it its appears
seems logged locked name not also just can cant could would should will may might be been being by as if so but
protection security learning takeover page example account user users username login logins host hostname machine
machines endpoint endpoints server servers system systems device devices service services password passwords
credentials session sessions activity details info information status issue issues alert alerts attempt attempts
failure failures compromise compromised access admin administrator policy policies agent agents detection response
management portal panel screen form field type types data file files""".split())
_ENGLISH_SUFFIXES = ("ing", "tion", "sion", "ity", "ness", "ment", "able", "ible", "ous")


def _identifier_like(tok: str) -> bool:
    low = tok.lower()
    if len(tok) < 2 or low in _FILLER or tok.replace(".", "").isdigit():
        return False
    if any(c in tok for c in "0123456789.-_"):
        return True
    return not low.endswith(_ENGLISH_SUFFIXES)


def _cue_values(pattern: re.Pattern, text: str) -> list[str]:
    out = []
    for m in pattern.finditer(text):
        tok = m.group(1).rstrip(".-_")
        if _identifier_like(tok):
            out.append(tok)
    return out


def _is_internal_fqdn(tok: str) -> bool:
    labels = tok.lower().split(".")
    if len(labels) < 2 or labels[-1] not in _INTERNAL_SUFFIXES or not all(_LABEL.fullmatch(x) for x in labels):
        return False
    # report.ad / notes.local look like file names: need >=3 labels or a digit on the left
    return len(labels) >= 3 or any(c.isdigit() for c in labels[0])


def _is_name_num(tok: str) -> bool:
    groups = tok.split("-")
    first = groups[0]
    if len(groups) < 2 or not all(g.isalnum() and g.isascii() for g in groups) or not groups[-1].isdigit():
        return False
    letters = sum(c.isalpha() for c in first)
    if letters < 2 or first.lower() in _NOT_HOST_PREFIX:
        return False
    # all-caps (WS, DC) or a letter+digit mix (SRV2); "Windows-10" and "win-2019" are not machines
    return first.isupper() or any(c.isdigit() for c in first)


def _hostnames(text: str) -> list[str]:
    found = []
    for raw in _TOKEN_SPLIT.findall(text):
        tok = raw.strip(".:!?")
        if not tok or any(c in tok for c in "\\/@=") or len(tok) > 253:
            continue
        if _is_internal_fqdn(tok) or _is_name_num(tok):
            found.append(tok)
    return found + _cue_values(_HOST_CUE, text)


def _usernames(text: str) -> list[str]:
    found = []
    for m in _DOMAIN_USER.finditer(text):
        nxt = text[m.end():m.end() + 1]
        if m.group(0).split("\\")[0].lower() not in _NOT_DOMAIN and nxt != "\\":
            found.append(m.group(0))
    return found + _cue_values(_USER_CUE, text) + _cue_values(_USER_VERB, text)


# ---- milestones ------------------------------------------------------------

_KEYWORD_KEY = {"first seen": "first_seen", "detected": "detected", "contained": "contained",
                "isolated": "contained", "recovered": "recovered", "restored": "recovered"}
# keyword, <=15 non-digit chars, optional "yesterday", optional ISO date, time (14:32 / 2:32pm), optional UTC/Z
_MILESTONE = re.compile(
    r"\b(?P<kw>first seen|detected|contained|isolated|recovered|restored)\b"
    r"[^\d\n]{0,15}?"
    r"(?P<yest>yesterday\s+(?:at\s+)?)?"
    r"(?:(?P<d1>\d{4}-\d{2}-\d{2})[^\d\n]{0,4}?)?"
    r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>am|pm)?\s*(?P<z>utc|z)?(?![\w])"
    r"(?:[ ,]{1,2}(?:on[ ])?(?P<d2>\d{4}-\d{2}-\d{2})(?!\d))?",
    re.IGNORECASE,
)
_UTC = ZoneInfo("UTC")


def _milestone_at(m: re.Match, tz: ZoneInfo, message_at: datetime) -> str | None:
    hour, minute, ap = int(m["h"]), int(m["m"]), (m["ap"] or "").lower()
    if minute > 59 or (ap and not 1 <= hour <= 12) or hour > 23:
        return None
    if ap:
        hour = hour % 12 + (12 if ap == "pm" else 0)
    zone = _UTC if m["z"] else tz
    if message_at.tzinfo is None:
        message_at = message_at.replace(tzinfo=_UTC)
    now = message_at.astimezone(zone)
    explicit = m["d1"] or m["d2"]
    if explicit:
        try:
            day = date.fromisoformat(explicit)
        except ValueError:
            return None
    else:
        day = now.date() - timedelta(days=1 if m["yest"] else 0)
    at = datetime.combine(day, time(hour, minute), tzinfo=zone)
    if not explicit and not m["yest"] and at > now:   # a bare time cannot be in the future
        at = datetime.combine(day - timedelta(days=1), time(hour, minute), tzinfo=zone)
    return at.isoformat()


# ---- entry point -------------------------------------------------------------

def _bounded(text: str) -> str:
    """Normalise, then cap length and drop pathological tokens so the regex
    work (including the shared indicator detector) stays cheap on hostile input."""
    text = normalize_slack_text((text or "")[:MAX_RAW_CHARS])[:MAX_TEXT_CHARS]
    return " ".join(t for t in text.split(" ") if len(t) <= MAX_TOKEN_CHARS)


def extract_candidates(text: str, *, tz: ZoneInfo, message_at: datetime,
                       exclude_emails: set[str] = frozenset()) -> list[Candidate]:
    text = _bounded(text)
    excluded = {e.lower() for e in exclude_emails}
    indicators = detect_indicators(text, limit=100)
    indicator_values = {i["value"].lower() for i in indicators}
    # hostname/username scans must not read inside URLs or emails
    bare = _URL_OR_EMAIL.sub(" ", text)
    # a cue token that is already a public indicator ("host evil.com") stays an indicator
    hosts = [h for h in _hostnames(bare) if h.lower() not in indicator_values or _is_internal_fqdn(h)]
    host_set = {h.lower() for h in hosts}

    out: dict[str, Candidate] = {}

    def add(c: Candidate) -> None:
        out.setdefault(c.value_key, c)

    for h in hosts:
        add(_artifact("hostname", h))
    for i in indicators:
        v = i["value"]
        if i["artifact_type"] == "email" and v.lower() in excluded:
            continue
        if i["artifact_type"] == "domain" and any(v.lower() == h or h.endswith("." + v.lower()) for h in host_set):
            continue
        add(_artifact(i["artifact_type"], v))
    for u in _usernames(bare):
        add(_artifact("username", u))

    for m in _MILESTONE.finditer(text):
        key = _KEYWORD_KEY[m["kw"].lower()]
        at = _milestone_at(m, tz, message_at)
        if at and key not in out:
            out[key] = Candidate("milestone", {"key": key, "at": at}, key)
    return list(out.values())
